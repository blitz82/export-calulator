from epms import sorting as s,store
from test_sorting import setup,payload
from test_sorting_beneficiaries import prepare,freeze
from test_sorting_payment_preparation import seed,create,edit
from test_sorting_cost_review import expense,pay


def count_state(app):
    with app.state.db.connect() as conn:
        return {table.name:[dict(row) for row in conn.execute(table.select().order_by(table.c.id)).mappings()] for table in [s.claims,s.allocations,s.expense_allocations,s.records,store.audit,store.declarations]}


def test_missing_sources_are_not_payment_eligibility_and_read_is_immutable(setup):
    app,client,base,*_=setup;claim,row=prepare(setup);before=count_state(app)
    response=client.get(base+f'/claims/{claim}/readiness');assert response.status_code==200,response.text
    result=response.json()
    assert {r['id'] for r in result['categories']}=={'DOCUMENTS','SOURCE','POLICY','OPERATIONS'}
    titles={r['title'] for r in result['items']}
    assert {'행별 계약 연결','회원별 귀속','비용 배부 미확인','명세 검토본 선택','지급 준비 대장 선택','P-01~15 확정 정책'}<=titles
    assert result['quantity']=='600' and result['paid_amount'] is None and not result['payment_eligible'] and not result['operational_approval']
    assert count_state(app)==before


def test_local_input_review_keeps_policy_and_operational_issues(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    entity=create(client,base,claim,statement).json()['id']
    assert edit(client,base,entity,'INPUT_REVIEWED',prepared_amount='100',planned_date='2027-03-01',documents_reviewed=True,basis='합성 준비').status_code==200
    result=client.get(base+f'/claims/{claim}/readiness',params=dict(preparation_id=entity)).json()
    assert result['statement_id']==statement
    assert any(r['title']=='회원별 준비 입력' and r['status']=='RECORDED_FOR_REVIEW' for r in result['items'])
    assert all(r['status']!='RECORDED_FOR_REVIEW' for r in result['items'] if r['category'] in {'POLICY','OPERATIONS'})
    assert not result['payment_eligible']


def test_mismatched_statement_and_preparation_are_explicit(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    entity=create(client,base,claim,statement).json()['id']
    second=freeze(client,base,claim,key='second-readiness-statement').json()['id']
    result=client.get(base+f'/claims/{claim}/readiness',params=dict(preparation_id=entity,statement_id=second)).json()
    assert any(r['title']=='명세·준비 대장 불일치' for r in result['items'])


def test_correction_reports_excess_and_stale_without_releasing(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    entity=create(client,base,claim,statement).json()['id']
    with app.state.db.begin() as conn:
        original=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,data=dict(original['data'],kg='500')))
    before=count_state(app)
    result=client.get(base+f'/claims/{claim}/readiness',params=dict(preparation_id=entity)).json()
    assert any(r['title']=='물리 순중량 초과' and '100 kg' in r['message'] for r in result['items'])
    assert any(r['title']=='준비 대장 재검토' for r in result['items'])
    assert count_state(app)==before


def test_contract_action_and_new_version_review(setup):
    app,client,base,evidence,facility,*_=setup;claim,row=prepare(setup)
    data=dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='600',start='2026-01-01',end='2026-12-31',parties='합성 당사자',terms='합성 조건')
    contract=client.post(base+'/records',json=dict(kind='CONTRACT',data=data)).json()['id']
    assert client.post(base+f'/claims/{claim}/lines/{row["line_id"]}/contract-review',json=dict(revision=1,contract_id=contract,reason='합성 연결',request_key='readiness-contract-link')).status_code==200
    result=client.get(base+f'/claims/{claim}/readiness').json()
    check=next(r for r in result['items'] if r['title']=='행별 계약 연결')
    assert check['status']=='RECORDED_FOR_REVIEW' and check['action']['field']=='contract' and check['action']['line_id']==row['line_id']
    assert client.post(base+f'/records/{contract}/revise',json=dict(revision=1,reason='합성 계약 개정',data=dict(data,quantity='700'))).status_code==200
    result=client.get(base+f'/claims/{claim}/readiness').json()
    assert any(r['title']=='행별 계약 연결' and r['category']=='SOURCE' for r in result['items'])


def test_unavailable_statement_scope_and_auth_are_not_success(setup):
    app,client,base,*_=setup;claim,row,statement=seed(setup)
    with app.state.db.begin() as conn:
        original=s.scoped(conn,s.records,1,statement)
        conn.execute(s.records.update().where(s.records.c.id==statement).values(data=dict(original['data'],seal='changed')))
    result=client.get(base+f'/claims/{claim}/readiness',params=dict(statement_id=statement)).json()
    assert any(r['status']=='UNAVAILABLE' for r in result['items'])
    assert not result['payment_eligible']
    assert client.get('/api/org/2/sorting/claims/'+str(claim)+'/readiness').status_code==404
    assert client.get(base+f'/claims/{claim}/readiness',params=dict(preparation_id=statement)).status_code==404
    assert client.get(base+f'/claims/{claim}/readiness',headers={'x-epms-token':'invalid'}).status_code==403


def test_cost_reconciliation_only_includes_linked_costs(setup):
    app,client,base,evidence,facility,*_=setup;claim,row=prepare(setup)
    cost=expense(client,base,facility,evidence,amount='1000');pay(client,base,cost,evidence,'readiness-cost','SUCCESS',amount='1000')
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='600')).status_code==200
    other=expense(client,base,facility,evidence,external_id='unrelated-cost',amount='1000')
    result=client.get(base+f'/claims/{claim}/readiness').json()
    rows=[r for r in result['items'] if r['title']=='비용·이체 대사']
    assert len(rows)==1 and rows[0]['reference']==cost and rows[0]['status']=='RECORDED_FOR_REVIEW'
    with app.state.db.begin() as conn:
        original=s.scoped(conn,s.expenses,1,cost)
        conn.execute(s.expenses.update().where(s.expenses.c.id==cost).values(data=dict(original['data'],amount='500')))
    result=client.get(base+f'/claims/{claim}/readiness').json()
    assert any(r['title']=='비용·이체 대사' and r['category']=='SOURCE' for r in result['items'])
