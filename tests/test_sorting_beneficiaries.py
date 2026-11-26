from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from openpyxl import load_workbook
from epms import sorting as s,store
from test_sorting import setup,payload
from test_sorting_cost_review import expense,pay


def prepare(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch,'600')).json()['id']
    line=client.get(base+f'/claims/{claim}').json()['lines'][0]['id']
    member=client.get(base+f'/claims/{claim}/beneficiaries').json()['members'][0]['id']
    row=dict(line_id=line,member_id=member,quantity='600',evidence_id=evidence,source_reference='synthetic-lot',basis='합성 귀속 근거',costs=[],reference_amount=None)
    return claim,row


def save(client,base,claim,rows,revision=1,key='beneficiary-request-001'):
    return client.post(base+f'/claims/{claim}/beneficiaries',json=dict(revision=revision,rows=rows,reason='합성 귀속 검토',request_key=key))


def freeze(client,base,claim,revision=2,key='statement-request-001'):
    return client.post(base+f'/claims/{claim}/beneficiary-statements',json=dict(revision=revision,reason='합성 명세 보존',request_key=key))


def test_version_idempotence_overflow_scope_and_no_payment(setup):
    app,client,base,*_=setup;claim,row=prepare(setup)
    result=save(client,base,claim,[row]);assert result.status_code==200,result.text
    assert save(client,base,claim,[row]).json()==result.json()
    assert save(client,base,claim,[dict(row,quantity='601')]).status_code==409
    assert save(client,base,claim,[row],key='stale-request-001').status_code==409
    assert save(client,base,claim,[dict(row,quantity='601')],revision=2,key='overflow-request').status_code==409
    assert client.get('/api/org/2/sorting/claims/'+str(claim)+'/beneficiaries').status_code==404
    assert client.post(base+f'/claims/{claim}/pay').status_code==403
    state=client.get(base+f'/claims/{claim}/beneficiaries').json()
    assert state['remaining'][0]['unassigned_quantity']=='0' and state['approved_amount'] is None and not state['operational_approval']


def test_member_split_cost_caps_and_member_only_output(setup):
    app,client,base,evidence,facility,_=setup;claim,row=prepare(setup)
    with app.state.db.begin() as conn:
        conn.execute(store.companies.insert().values(number='SECOND-SYNTHETIC'))
        other=conn.execute(store.memberships.insert().values(org=1,number='SECOND-SYNTHETIC',revision=1,data=dict(name='다른가상회원'))).inserted_primary_key[0]
    cost=expense(client,base,facility,evidence,amount='1000');pay(client,base,cost,evidence,'cost-transfer','SUCCESS',amount='1000')
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='1000')).status_code==200
    rows=[dict(row,quantity='400',costs=[dict(expense_id=cost,amount='700')],reference_amount='123'),dict(row,member_id=other,quantity='200',costs=[dict(expense_id=cost,amount='301')])]
    assert save(client,base,claim,rows).status_code==409
    rows[1]['costs'][0]['amount']='300'
    assert save(client,base,claim,rows).status_code==200
    saved=freeze(client,base,claim);assert saved.status_code==200,saved.text
    entity=saved.json()['id'];path=base+f'/beneficiary-statements/{entity}'
    assert freeze(client,base,claim).json()==saved.json()
    body=client.get(path,params=dict(member_id=row['member_id'])).json()
    assert len(body['rows'])==1 and 'remaining' not in body and 'costs' not in body
    assert '다른가상회원' not in str(body) and 'SECOND-SYNTHETIC' not in str(body)
    workbook=load_workbook(BytesIO(client.get(path+'/report',params=dict(member_id=row['member_id'])).content))
    assert '미귀속 물량·비용' not in workbook.sheetnames
    cells=[cell.value for sheet in workbook for cellrow in sheet for cell in cellrow]
    assert '다른가상회원' not in str(cells) and '123' in cells
    workbook.close()
    assert '다른가상회원' not in client.get(path+'/print',params=dict(member_id=row['member_id'])).text
    assert client.get(path,params=dict(member_id=99999)).status_code==404
    assert client.get(path+'/report?official=true').status_code==403


def test_source_change_stales_draft_but_frozen_output_is_immutable(setup):
    app,client,base,evidence,*_=setup;claim,row=prepare(setup)
    assert save(client,base,claim,[row]).status_code==200
    entity=freeze(client,base,claim).json()['id'];path=base+f'/beneficiary-statements/{entity}'
    original=client.get(path).json()
    with app.state.db.begin() as conn:
        source=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,data=dict(source['data'],kg='500')))
    state=client.get(base+f'/claims/{claim}/beneficiaries').json();assert state['stale']
    assert freeze(client,base,claim,key='new-statement-key').status_code==409
    assert client.get(path).json()==original
    assert client.post(base+f'/claims/{claim}/transition',json=dict(revision=2,action='WITHDRAW',reason='철회 시험')).status_code==200
    assert save(client,base,claim,[row],revision=3,key='withdrawn-attempt').status_code==409
    assert client.get(path).json()==original


def test_duplicate_rows_foreign_members_and_evidence_integrity(setup):
    app,client,base,evidence,*_=setup;claim,row=prepare(setup)
    assert save(client,base,claim,[dict(row,quantity='200'),dict(row,quantity='200')]).status_code==409
    with app.state.db.begin() as conn:
        foreign=conn.execute(store.memberships.insert().values(org=2,number='1234567834',revision=1,data=dict(name='타품목회원'))).inserted_primary_key[0]
    assert save(client,base,claim,[dict(row,member_id=foreign)]).status_code==404
    with app.state.db.begin() as conn:conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'changed'))
    assert save(client,base,claim,[row]).status_code==409


def test_empty_replacement_preserves_history_and_concurrent_revision(setup):
    app,client,base,*_=setup;claim,row=prepare(setup)
    def attempt(key):return save(client,base,claim,[row],key=key).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(attempt,['parallel-first','parallel-second']))==[200,409]
    assert save(client,base,claim,[],revision=2,key='clear-attribution').status_code==200
    state=client.get(base+f'/claims/{claim}/beneficiaries').json()
    assert len(state['history'])==2 and state['rows']==[] and state['remaining'][0]['unassigned_quantity']=='600'
    assert freeze(client,base,claim,revision=3).status_code==409


def test_many_declarations_many_facilities_and_cost_facility_scope(setup):
    app,client,base,evidence,facility,batch=setup
    facility2=client.post(base+'/facilities',json=dict(name='다른가상시설',product='가상품목',member_number='1234567834',designation='OTHER',valid_from='2026-01-01',valid_to='2027-12-31',evidence_id=evidence)).json()['id']
    batch2=client.post(base+'/batches',json=dict(facility_id=facility2,external_id='OTHER',work_date='2026-03-02',product='가상품목',quantity='700',unit='kg',evidence_id=evidence)).json()['id']
    with app.state.db.begin() as conn:
        conn.execute(store.declarations.insert().values(id=2,org=1,revision=1,deleted=0,data=dict(number='SYNTHETIC-26-000002X',line='002',version='1',kg='500',status='유효',scope='대상')))
    body=payload(batch);body['lines']=[dict(declaration_id=d,declaration_revision=1,batch_id=b,batch_revision=1,quantity=q) for d,b,q in [(1,batch,'400'),(1,batch2,'600'),(2,batch,'300')]]
    claim=client.post(base+'/claims',json=body).json()['id']
    review=client.get(base+f'/claims/{claim}').json();member=client.get(base+f'/claims/{claim}/beneficiaries').json()['members'][0]['id']
    cost=expense(client,base,facility,evidence,amount='1000');pay(client,base,cost,evidence,'many-cost','SUCCESS',amount='1000')
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='1000')).status_code==200
    rows=[dict(line_id=line['id'],member_id=member,quantity=line['quantity'],evidence_id=evidence,source_reference='synthetic-lot',basis='<script>가상 근거</script>',costs=[],reference_amount=None) for line in review['lines']]
    rows[1]['costs']=[dict(expense_id=cost,amount='100')]
    assert save(client,base,claim,rows).status_code==409
    rows[1]['costs']=[];rows[0]['costs']=[dict(expense_id=cost,amount='700')];rows[2]['costs']=[dict(expense_id=cost,amount='300')]
    assert save(client,base,claim,rows).status_code==200
    entity=freeze(client,base,claim).json()['id'];path=base+f'/beneficiary-statements/{entity}'
    from epms.sorting_beneficiaries import totals
    frozen=client.get(path).json()
    assert [r[1] for r in totals(frozen['rows'],'declaration_number')]==['1000','300']
    assert [r[1] for r in totals(frozen['rows'],'facility_name')]==['700','600']
    assert totals(frozen['rows'],'all')[0][1:] == ['1300',None,None]
    from decimal import Decimal
    assert sum(Decimal(cost['amount']) for row in frozen['rows'] for cost in row['costs'])==Decimal('1000')
    html=client.get(path+'/print').text
    assert '<script>가상 근거</script>' not in html and '&lt;script&gt;' in html
    book=load_workbook(BytesIO(client.get(path+'/report').content))
    assert all(cell.data_type!='f' for sheet in book for row in sheet for cell in row)
    book.close()


def test_partial_unknown_and_zero_amounts_remain_distinct_and_request_tamper_rejected(setup):
    app,client,base,*_=setup;claim,row=prepare(setup)
    assert save(client,base,claim,[dict(row,quantity='200',reference_amount='0')]).status_code==200
    entity=freeze(client,base,claim).json()['id'];body=client.get(base+f'/beneficiary-statements/{entity}').json()
    assert body['remaining'][0]['unassigned_quantity']=='400' and body['rows'][0]['reference_amount']=='0'
    assert body['rows'][0]['approved_amount'] is None and body['rows'][0]['paid_amount'] is None
    assert body['rows'][0]['cost_amount'] is None
    assert save(client,base,claim,[dict(row,paid_amount='1')],revision=2,key='tamper-paid-field').status_code==422
    with app.state.db.begin() as conn:
        record=s.scoped(conn,s.records,1,entity)
        snapshot=dict(record['data']['snapshot'],paid_amount='1')
        conn.execute(s.records.update().where(s.records.c.id==entity).values(data=dict(record['data'],snapshot=snapshot)))
    assert client.get(base+f'/beneficiary-statements/{entity}').status_code==409


def test_member_change_cost_payment_change_and_attribution_in_snapshot(setup):
    app,client,base,evidence,facility,*_=setup;claim,row=prepare(setup)
    assert save(client,base,claim,[row]).status_code==200
    baseline=client.post(base+f'/claims/{claim}/snapshots',json=dict(revision=2,reason='수혜자 포함 기준선',request_key='beneficiary-baseline')).json()['id']
    snapshot=client.get(base+f'/snapshots/{baseline}/report').json()
    assert any(r['kind']=='BENEFICIARY_REVIEW' for r in snapshot['data']['snapshot']['records'])
    with app.state.db.begin() as conn:
        member=s.scoped(conn,store.memberships,1,row['member_id'])
        conn.execute(store.memberships.update().where(store.memberships.c.id==member['id']).values(revision=2,data=dict(member['data'],name='변경가상회원')))
    assert client.get(base+f'/claims/{claim}/beneficiaries').json()['stale']


def test_cost_source_integrity_and_feature_gate(setup,tmp_path,monkeypatch):
    app,client,base,evidence,facility,*_=setup;claim,row=prepare(setup)
    separate=client.post(base+'/evidence?name=synthetic-cost-proof.txt',content=b'synthetic separate cost').json()['id']
    cost=expense(client,base,facility,separate,amount='1000');pay(client,base,cost,separate,'integrity-cost','SUCCESS',amount='1000')
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='1000')).status_code==200
    rows=[dict(row,costs=[dict(expense_id=cost,amount='600')])]
    with app.state.db.begin() as conn:conn.execute(s.evidence.update().where(s.evidence.c.id==separate).values(content=b'changed'))
    assert save(client,base,claim,rows).status_code==409
    assert client.get(base+f'/claims/{claim}/beneficiaries',headers={'x-epms-token':'invalid'}).status_code==403
    from epms.api import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('EPMS_SORTING_ENABLED','0')
    off=create_app(tmp_path/'off.db')
    with TestClient(off) as off_client:
        off_client.headers['x-epms-token']=off.state.token
        assert off_client.get('/api/org/1/sorting/claims/1/beneficiaries').status_code==404
        assert off_client.post('/api/org/1/sorting/claims/1/beneficiaries',json=dict(revision=1,rows=[],reason='합성',request_key='off-request-key')).status_code==404
    off.state.db.dispose()
