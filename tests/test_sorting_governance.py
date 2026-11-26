from decimal import Decimal
from epms import sorting as s,store
from test_sorting import setup,payload
from test_sorting_cost_review import expense,pay


def draft(evidence,**changes):
    return dict(dict(policy_id='P-07',year=2027,product='가상품목',start='2027-01-01',end='2027-12-31',state='DRAFT',choice='가상 단가 검토',basis='합성 검토 근거',reason='최초 초안',evidence_id=evidence,request_key='policy-request-001'),**changes)


def test_policy_versions_overlap_idempotence_scope_no_approval(setup):
    app,client,base,evidence,*_=setup
    first=client.post(base+'/policy-drafts',json=draft(evidence));assert first.status_code==200,first.text
    entity=first.json()['id']
    assert client.post(base+'/policy-drafts',json=draft(evidence)).json()['id']==entity
    assert client.post(base+'/policy-drafts',json=draft(evidence,request_key='another-request')).status_code==409
    change=draft(evidence,previous_id=entity,previous_revision=1,request_key='policy-request-002',choice='개정 검토')
    second=client.post(base+'/policy-drafts',json=change);assert second.status_code==200,second.text
    assert second.json()['revision']==2
    assert client.post(base+'/policy-drafts',json=dict(change,request_key='third-request')).status_code==409
    assert client.post(base+'/policy-drafts',json=draft(evidence,state='APPROVED')).status_code==422
    assert client.post('/api/org/2/sorting/policy-drafts',json=draft(evidence)).status_code==404
    view=client.get(base+'/policy-drafts').json()
    assert view['versions'][0]['superseded'] and not view['operational_approval']
    assert all(p['status']=='POLICY_PENDING' for p in view['policies'])


def test_record_revision_preserves_contract_and_rejects_stale(setup):
    app,client,base,evidence,facility,*_=setup
    data=dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='100',start='2026-01-01',end='2026-12-31',parties='synthetic-party',terms='원 계약')
    entity=client.post(base+'/records',json=dict(kind='CONTRACT',data=data)).json()['id']
    revised=client.post(base+f'/records/{entity}/revise',json=dict(revision=1,reason='약정 변경',data=dict(data,quantity='120')))
    assert revised.status_code==200,revised.text
    assert client.post(base+f'/records/{entity}/revise',json=dict(revision=1,reason='경합 요청',data=data)).status_code==409
    rows={r['id']:r for r in client.get(base+'/records').json()}
    assert rows[entity]['data']['quantity']=='100'
    assert rows[revised.json()['id']]['data']['quantity']=='120' and rows[revised.json()['id']]['data']['previous_id']==entity


def test_snapshot_compare_correction_candidate_immutable_and_guard(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch,'1000')).json()['id']
    body=dict(revision=1,reason='검토 기준선',request_key='snapshot-key-001')
    saved=client.post(base+f'/claims/{claim}/snapshots',json=body)
    assert saved.status_code==200,saved.text
    entity=saved.json()['id'];report=client.get(base+f'/snapshots/{entity}/report').content
    assert client.post(base+f'/claims/{claim}/snapshots',json=body).json()['id']==entity
    assert client.get(base+f'/snapshots/{entity}/compare').json()['status']=='UNCHANGED'
    with app.state.db.begin() as conn:
        row=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,data=dict(row['data'],kg='900')))
    comparison=client.get(base+f'/snapshots/{entity}/compare').json()
    assert comparison['status']=='CHANGED'
    assert comparison['quantity_candidates'][0]['excess']=='100'
    candidate=client.post(base+f'/snapshots/{entity}/change-candidate',json=dict(current_hash=comparison['current_hash'],reason='100kg 초과 검토'))
    assert candidate.status_code==200,candidate.text
    assert not candidate.json()['recovery_executed']
    assert client.get(base+f'/snapshots/{entity}/report').content==report
    assert client.get(base+f'/claims/{claim}').json()['quantity']=='1000'
    assert client.get('/api/org/2/sorting/snapshots/'+str(entity)+'/report').status_code==404
    assert client.post(base+f'/claims/{claim}/approve').status_code==403
    with app.state.db.begin() as conn:
        row=s.scoped(conn,s.records,1,entity)
        conn.execute(s.records.update().where(s.records.c.id==entity).values(data=dict(row['data'],seal='0'*64)))
    assert client.get(base+f'/snapshots/{entity}/report').status_code==409


def test_group_discovers_split_claims_union_deductions_and_budget_null_zero(setup):
    app,client,base,evidence,facility,batch=setup
    first=client.post(base+'/claims',json=payload(batch,'600')).json()['id']
    second=client.post(base+'/claims',json=payload(batch,'400','group-claim-002')).json()['id']
    cost=expense(client,base,facility,evidence,amount='10000')
    pay(client,base,cost,evidence,'group-success','SUCCESS',amount='10000')
    for claim,amount in ((first,'6000'),(second,'4000')):
        assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount=amount)).status_code==200
    body=dict(claims=[dict(id=first,revision=1)],unit_price='10',available_budget='8000',commitments=[dict(portion_id='same-part',amount='1000'),dict(portion_id='same-part',amount='1000')])
    response=client.post(base+'/group-review',json=body);assert response.status_code==200,response.text
    result=response.json()
    assert result['groups'][0]['claim_ids']==[first,second]
    assert result['reference_total']=='10000' and result['deduction']=='1000' and result['hypothetical_remaining']=='8000'
    assert result['payable_amount'] is None and not result['operational_approval']
    assert client.post(base+'/group-review',json=dict(body,available_budget='0')).json()['hypothetical_remaining']=='0'
    assert client.post(base+'/group-review',json=dict(body,available_budget=None)).json()['hypothetical_remaining'] is None
    assert client.post(base+'/group-review',json=dict(body,commitments=None)).json()['hypothetical_remaining'] is None
    assert client.post(base+'/group-review',json=dict(body,commitments=[dict(portion_id='same',amount='1'),dict(portion_id='same',amount='2')])).status_code==409
    assert client.post(base+'/group-review',json=dict(body,claims=[dict(id=first,revision=99)])).status_code==409


def test_group_unknown_cost_and_policy_remain_unconfirmed(setup):
    app,client,base,evidence,facility,batch=setup
    first=client.post(base+'/claims',json=payload(batch,'600')).json()['id']
    response=client.post(base+'/group-review',json=dict(claims=[dict(id=first,revision=1)],unit_price='235',available_budget='10000',commitments=[]))
    assert response.status_code==200,response.text
    assert response.json()['reference_total'] is None and response.json()['hypothetical_remaining'] is None


def test_facility_cas_history_identity_and_snapshot_difference(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    snapshot=client.post(base+f'/claims/{claim}/snapshots',json=dict(revision=1,reason='시설 개정 전',request_key='facility-baseline')).json()['id']
    old=client.get(base+'/facilities').json()[0]
    values={key:old['data'][key] for key in ('name','product','member_number','designation','valid_from','valid_to','evidence_id')}
    body=dict(revision=1,reason='주소 수정',data=dict(values,address='합성 주소',operator='합성 운영자'))
    result=client.post(base+f'/facilities/{facility}/revise',json=body)
    assert result.status_code==200,result.text
    assert result.json()['revision']==2
    assert client.post(base+f'/facilities/{facility}/revise',json=body).status_code==409
    history=client.get(base+f'/facilities/{facility}/history').json()
    assert history[0]['data']['before']==old['data'] and history[0]['data']['after']['address']=='합성 주소'
    assert client.post(base+f'/facilities/{facility}/revise',json=dict(body,revision=2,data=dict(body['data'],designation='OTHER'))).status_code==422
    assert any(c['part']=='facilities' for c in client.get(base+f'/snapshots/{snapshot}/compare').json()['changes'])


def test_dispatch_capacity_preserves_direct_and_dispatch_scopes(setup):
    app,client,base,evidence,facility,batch=setup
    dispatch=client.post(base+'/dispatches',json=dict(batch_id=batch,external_id='dispatch-capacity',exporter_id='synthetic-exporter',date='2026-03-02',quantity='1000',evidence_id=evidence)).json()['id']
    query=dict(declaration_id=1,declaration_revision=1,batch_id=batch)
    assert client.get(base+'/capacity',params=query).json()['available']=='0'
    assert client.get(base+'/capacity',params=dict(query,dispatch_id=dispatch)).json()['available']=='1000'
    body=payload(batch,'600');body['lines'][0]['dispatch_id']=dispatch
    assert client.post(base+'/claims',json=body).status_code==200
    assert client.get(base+'/capacity',params=dict(query,dispatch_id=dispatch)).json()['available']=='400'
    assert client.get('/api/org/2/sorting/dispatches',params=dict(batch_id=batch)).status_code==404


def test_policy_retirement_no_implicit_activation_and_evidence_tamper(setup):
    app,client,base,evidence,*_=setup
    one=client.post(base+'/policy-drafts',json=draft(evidence)).json()['id']
    retired=draft(evidence,state='RETIRED',previous_id=one,previous_revision=1,request_key='policy-retire-001')
    assert client.post(base+'/policy-drafts',json=retired).status_code==200
    assert client.post(base+'/policy-drafts',json=draft(evidence,request_key='new-draft-001')).status_code==200
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'changed'))
    assert client.post(base+'/policy-drafts',json=draft(evidence,request_key='tamper-req-001')).status_code==409


def test_snapshot_stale_revision_and_current_missing_source(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    assert client.post(base+f'/claims/{claim}/snapshots',json=dict(revision=99,reason='stale',request_key='stale-snapshot-001')).status_code==409
    entity=client.post(base+f'/claims/{claim}/snapshots',json=dict(revision=1,reason='검토',request_key='valid-snapshot-001')).json()['id']
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.delete().where(s.evidence.c.id==evidence))
    assert client.get(base+f'/snapshots/{entity}/compare').json()['status']=='CURRENT_UNAVAILABLE'


def test_group_detects_cost_integrity_problem(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    cost=expense(client,base,facility,evidence)
    pay(client,base,cost,evidence,'cost-group','SUCCESS')
    client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='1000'))
    with app.state.db.begin() as conn:
        row=s.scoped(conn,s.expenses,1,cost)
        conn.execute(s.expenses.update().where(s.expenses.c.id==cost).values(data=dict(row['data'],amount='500')))
    response=client.post(base+'/group-review',json=dict(claims=[dict(id=claim,revision=1)],unit_price='235',available_budget='10000',commitments=[]))
    assert response.status_code==409,response.text


def test_settlement_payment_date_and_plan_period_validation(setup):
    app,client,base,evidence,facility,*_=setup
    contract=dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='100',start='2026-01-01',end='2026-12-31',parties='synthetic-party',terms='합성 조건')
    entity=client.post(base+'/records',json=dict(kind='CONTRACT',data=contract)).json()['id']
    values=dict(contract_id=entity,evidence_id=evidence,producer_id='synthetic-farm',sales='100',sorting_deduction='0',other_deduction='0',fee='0',adjustment_add='0',adjustment_subtract='0',paid='100',due_date='2026-03-31',state='COMPLETED',paid_date='2026-03-30',payment_evidence_id=evidence)
    result=client.post(base+'/records',json=dict(kind='PRODUCER_SETTLEMENT',data=values));assert result.status_code==200,result.text
    record=next(r for r in client.get(base+'/records').json() if r['id']==result.json()['id'])
    assert record['data']['payment_date_status']=='RECORDED_FOR_REVIEW' and not record['data']['actual_payment_verified']
    assert client.post(base+'/records',json=dict(kind='PRODUCER_SETTLEMENT',data=dict(values,paid_date='invalid'))).status_code==422
    plan=dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='100',period='2026-13',change_reason='합성 계획')
    assert client.post(base+'/records',json=dict(kind='PLAN',data=plan)).status_code==422
    assert client.post(base+'/records',json=dict(kind='PLAN',data=dict(plan,period='2026-03'))).status_code==200


def test_contract_link_version_history_revoke_and_stale_claim(setup):
    app,client,base,evidence,facility,batch=setup
    values=dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='1000',start='2026-01-01',end='2026-12-31',parties='synthetic',terms='합성 계약')
    contract=client.post(base+'/records',json=dict(kind='CONTRACT',data=values)).json()['id']
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    line=client.get(base+f'/claims/{claim}').json()['lines'][0]['id'];path=base+f'/claims/{claim}/lines/{line}/contract-review'
    body=dict(revision=1,contract_id=contract,reason='계약 원문 연결',request_key='contract-link-001')
    result=client.post(path,json=body);assert result.status_code==200,result.text
    assert client.post(path,json=body).json()['id']==result.json()['id']
    assert client.get(path).json()['status']=='RECORDED_FOR_REVIEW'
    assert client.post(path,json=dict(body,request_key='stale-link-002')).status_code==409
    client.post(base+f'/records/{contract}/revise',json=dict(revision=1,reason='약정 변경',data=dict(values,quantity='1200')))
    assert client.get(path).json()['status']=='REVIEW_REQUIRED'
    assert client.post(path,json=dict(revision=2,contract_id=None,reason='재검토 해제',request_key='contract-unlink-001')).status_code==200
    current=client.get(path).json()
    assert current['contract_id'] is None and len(current['history'])==2


def test_fixed_cost_rate_strategy_and_unapproved_mixed_rejection():
    import pytest
    from epms import sorting_finance as finance
    assert Decimal(finance.calculate_strategy('TEST','FIXED_COST_RATE','100','718',rate='0.8'))==Decimal('57440')
    assert Decimal(finance.calculate_strategy('TEST','FIXED_COST_RATE','100','718',rate='0'))==0
    assert finance.calculate_strategy('TEST','FIXED_COST_RATE','100','718',rate=None) is None
    with pytest.raises(s.SortingError):
        finance.calculate_strategy('TEST','MIXED','100','718')


def test_snapshot_and_group_do_not_choose_ambiguous_global_source(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    entity=client.post(base+f'/claims/{claim}/snapshots',json=dict(revision=1,reason='충돌 전',request_key='global-source-snapshot')).json()['id']
    with app.state.db.begin() as conn:
        source=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.insert().values(org=2,revision=1,deleted=0,data=source['data']))
    result=client.get(base+f'/snapshots/{entity}/compare').json()
    assert result['status']=='CHANGED' and result['quantity_candidates']==[]
    group=client.post(base+'/group-review',json=dict(claims=[dict(id=claim,revision=1)],unit_price='235',available_budget='1000',commitments=[]))
    assert group.status_code==409
