from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from openpyxl import load_workbook
from epms import sorting as s,store
from test_sorting import setup
from test_sorting_beneficiaries import prepare,save,freeze


def seed(setup,quantity='600'):
    app,client,base,*_=setup;claim,row=prepare(setup)
    assert save(client,base,claim,[dict(row,quantity=quantity)]).status_code==200
    statement=freeze(client,base,claim).json()['id']
    return claim,row,statement


def create(client,base,claim,statement,key='prepare-create-001'):
    return client.post(base+f'/claims/{claim}/payment-preparations',json=dict(claim_revision=2,statement_id=statement,reason='합성 지급 준비',request_key=key))


def edit(client,base,entity,state,revision=1,key='prepare-edit-001',**updates):
    view=client.get(base+f'/payment-preparations/{entity}').json()
    members=[dict(member_id=r['member_id'],prepared_amount=r['prepared_amount'],planned_date=r['planned_date'],documents_reviewed=r['documents_reviewed'],basis=r['basis']) for r in view['members']]
    for row in members:row.update(updates)
    return client.post(base+f'/payment-preparations/{entity}/revise',json=dict(revision=revision,state=state,members=members,reason='합성 준비 개정',request_key=key))


def test_create_idempotence_one_active_and_input_review_is_not_payment(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    first=create(client,base,claim,statement);assert first.status_code==200,first.text
    entity=first.json()['id'];path=base+f'/payment-preparations/{entity}'
    assert create(client,base,claim,statement).json()==first.json()
    assert create(client,base,claim,statement,key='duplicate-preparation').status_code==409
    assert edit(client,base,entity,'INPUT_REVIEWED').status_code==409
    result=edit(client,base,entity,'INPUT_REVIEWED',prepared_amount='100',planned_date='2027-03-01',documents_reviewed=True,basis='합성 수동 준비액 근거')
    assert result.status_code==200,result.text
    assert edit(client,base,entity,'INPUT_REVIEWED',prepared_amount='100',planned_date='2027-03-01',documents_reviewed=True,basis='합성 수동 준비액 근거').json()==result.json()
    view=client.get(path).json()
    assert view['prepared_total']=='100' and view['approved_amount'] is None and view['paid_amount'] is None
    assert '미승인' in view['state_label'] and not view['operational_approval']
    assert view['basis_issues']
    from epms.sorting_snapshots import capture
    with app.state.db.connect() as conn:assert any(r['kind']=='PAYMENT_PREPARATION_REVIEW' for r in capture(conn,1,claim)['records'])
    assert client.post(path+'/confirm').status_code==403
    assert client.get(path+'/report?official=true').status_code==403
    review=client.get(base+f'/claims/{claim}').json();assert review['revision']==2 and review['quantity']=='600'


def test_partial_attribution_cancel_preserves_reservations_and_recreate(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup,'200')
    entity=create(client,base,claim,statement).json()['id']
    assert edit(client,base,entity,'INPUT_REVIEWED',prepared_amount='100',planned_date='2027-03-01',documents_reviewed=True,basis='합성').status_code==409
    assert edit(client,base,entity,'CANCELLED',key='prepare-cancel-001').status_code==200
    assert client.get(base+f'/claims/{claim}').json()['quantity']=='600'
    assert edit(client,base,entity,'DRAFT',revision=2,key='reopen-cancelled').status_code==409
    assert create(client,base,claim,statement,key='recreate-preparation').status_code==200


def test_source_changes_only_allow_hold_or_cancel_and_immutable_basis(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    entity=create(client,base,claim,statement).json()['id']
    with app.state.db.begin() as conn:
        member=s.scoped(conn,store.memberships,1,row['member_id'])
        conn.execute(store.memberships.update().where(store.memberships.c.id==member['id']).values(revision=2,data=dict(member['data'],name='변경합성회원')))
    view=client.get(base+f'/payment-preparations/{entity}').json()
    assert view['stale'] and view['members'][0]['member_name']=='가상조합'
    assert edit(client,base,entity,'DRAFT').status_code==409
    assert edit(client,base,entity,'HOLD',key='hold-changed-source').status_code==200
    assert edit(client,base,entity,'CANCELLED',revision=2,key='cancel-changed-source').status_code==200
    assert create(client,base,claim,statement,key='stale-new-plan').status_code==409


def test_concurrent_creation_and_revision_cas(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:create(client,base,claim,statement,key),['parallel-plan-001','parallel-plan-002']))
    assert sorted(r.status_code for r in results)==[200,409]
    entity=next(r.json()['id'] for r in results if r.status_code==200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:edit(client,base,entity,'DRAFT',key=key,prepared_amount='0',basis='합성 0원 준비 검토'),['parallel-edit-001','parallel-edit-002']))
    assert sorted(r.status_code for r in results)==[200,409]
    assert client.get(base+f'/payment-preparations/{entity}').json()['prepared_total']=='0'


def test_member_export_scope_unknown_amounts_and_no_operational_state(setup):
    app,client,base,*_=setup;claim,row=prepare(setup)
    with app.state.db.begin() as conn:
        conn.execute(store.companies.insert().values(number='SYNTHETIC-OTHER'))
        other=conn.execute(store.memberships.insert().values(org=1,number='SYNTHETIC-OTHER',revision=1,data=dict(name='비공유가상회원'))).inserted_primary_key[0]
    assert save(client,base,claim,[dict(row,quantity='300'),dict(row,member_id=other,quantity='300')]).status_code==200
    statement=freeze(client,base,claim).json()['id'];entity=create(client,base,claim,statement).json()['id'];path=base+f'/payment-preparations/{entity}'
    view=client.get(path).json();assert view['prepared_total'] is None
    personal=client.get(path,params=dict(member_id=row['member_id'])).json()
    assert len(personal['members'])==1 and '비공유가상회원' not in str(personal)
    book=load_workbook(BytesIO(client.get(path+'/report',params=dict(member_id=row['member_id'])).content))
    assert '비공유가상회원' not in str([cell.value for sheet in book for cells in sheet for cell in cells]);book.close()
    assert '비공유가상회원' not in client.get(path+'/print',params=dict(member_id=row['member_id'])).text
    assert client.get(path+'?member_id=99999').status_code==404
    assert client.get('/api/org/2/sorting/payment-preparations/'+str(entity)).status_code==404
    data=dict(revision=1,state='PAID',members=[],reason='합성',request_key='inject-payment-status')
    assert client.post(path+'/revise',json=data).status_code==422


def test_fixed_member_set_invalid_date_and_hash_guard(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    entity=create(client,base,claim,statement).json()['id'];path=base+f'/payment-preparations/{entity}'
    assert edit(client,base,entity,'DRAFT',prepared_amount='100',planned_date='2027-99-99',basis='합성').status_code==422
    invalid=dict(revision=1,state='DRAFT',members=[dict(member_id=999,prepared_amount=None,planned_date=None,documents_reviewed=False,basis='')],reason='합성',request_key='different-member-set')
    assert client.post(path+'/revise',json=invalid).status_code==409
    with app.state.db.begin() as conn:
        record=s.scoped(conn,s.records,1,entity)
        conn.execute(s.records.update().where(s.records.c.id==entity).values(data=dict(record['data'],state='INPUT_REVIEWED')))
    assert client.get(path).status_code==409
