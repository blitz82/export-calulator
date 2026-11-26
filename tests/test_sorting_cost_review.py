import pytest
from epms import sorting as s
from test_sorting import setup,payload


QUERY=dict(start='2026-03',end='2026-03')


def expense(client,base,facility,evidence,**changes):
    data=dict(facility_id=facility,external_id='synthetic-cost',month='2026-03',category='EXPORT_SORTING_LABOR',amount='1000.000001',separated=True,evidence_id=evidence,separation_evidence_id=evidence,pseudonym='synthetic-worker')
    data.update(changes)
    result=client.post(base+'/expenses',json=data)
    assert result.status_code==200,result.text
    return result.json()['id']


def pay(client,base,entity,evidence,key,status,amount='1000.000001',retry=None):
    response=client.post(base+'/cost-payment-history',json=dict(expense_id=entity,transaction_id=key,status=status,amount=amount,date='2026-04-01',evidence_id=evidence,retry_of=retry))
    assert response.status_code==200,response.text
    return response.json()['id']


def test_failed_retry_success_and_partial_allocation_read_only(setup):
    app,client,base,evidence,facility,batch=setup
    entity=expense(client,base,facility,evidence)
    failed=pay(client,base,entity,evidence,'failed','FAILED')
    retry=pay(client,base,entity,evidence,'retry','SUCCESS',retry=failed)
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=entity,amount='400')).status_code==200
    before=client.get(base+'/records').json()
    response=client.get(base+'/cost-review',params=QUERY)
    assert response.status_code==200,response.text
    value=response.json();row=value['rows'][0]
    assert row['success']=='1000.000001' and row['candidate']=='1000.000001'
    assert row['allocated']=='400' and row['balance']=='600.000001'
    assert row['payment_totals']['FAILED']=='1000.000001'
    assert row['payments'][1]['retry_of']==failed and row['payments'][1]['id']==retry
    assert row['payments'][1]['date']=='2026-04-01'  # Attribution is cost month, not transfer date.
    assert row['issues']==[] and len(row['evidence'])==1
    assert not row['payment_verified'] and value['payable_amount'] is None
    assert client.get(base+'/records').json()==before
    revision=client.get(base+f'/claims/{claim}').json()['revision']
    assert client.post(base+f'/claims/{claim}/transition',json=dict(revision=revision,action='WITHDRAW',reason='합성 철회')).status_code==200
    row=client.get(base+'/cost-review',params=QUERY).json()['rows'][0]
    assert row['allocated']=='0' and row['balance']=='1000.000001'
    assert row['allocations'][0]['active'] is False


def test_excluded_pending_and_no_payment_remain_separate(setup):
    app,client,base,evidence,facility,batch=setup
    excluded=expense(client,base,facility,evidence,category='PACKAGING')
    pay(client,base,excluded,evidence,'excluded-success','SUCCESS')
    expense(client,base,facility,evidence,external_id='pending',category='INSURANCE')
    expense(client,base,facility,evidence,external_id='no-payment')
    rows=client.get(base+'/cost-review',params=QUERY).json()['rows']
    assert [r['eligibility'] for r in rows]==['EXCLUDED','PENDING','REFERENCE_CANDIDATE']
    assert all(r['candidate']=='0' for r in rows)
    assert rows[0]['success']=='1000.000001' and rows[2]['payments']==[]


def test_hash_change_and_overallocation_not_hidden_or_offset(setup):
    app,client,base,evidence,facility,batch=setup
    entity=expense(client,base,facility,evidence)
    pay(client,base,entity,evidence,'success','SUCCESS')
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    assert client.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=entity,amount='900')).status_code==200
    other=expense(client,base,facility,evidence,external_id='separate')
    pay(client,base,other,evidence,'other-success','SUCCESS')
    with app.state.db.begin() as conn:
        row=s.scoped(conn,s.expenses,1,entity)
        conn.execute(s.expenses.update().where(s.expenses.c.id==entity).values(data=dict(row['data'],amount='800')))
        conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'changed'))
    value=client.get(base+'/cost-review',params=QUERY).json();row=value['rows'][0]
    assert row['balance']=='-100' and row['overallocated']=='100'
    assert value['totals']['available']=='1000.000001' and value['totals']['overallocated']=='100'
    assert row['evidence'][0]['integrity']=='CHANGED' and len(row['issues'])>=3
    assert value['operational_approval'] is False


@pytest.mark.parametrize('query',[dict(start='2026-13'),dict(start='0000-01'),dict(start='202603'),dict(start='2026-04')])
def test_month_range_invalid(setup,query):
    app,client,base,*_=setup
    assert client.get(base+'/cost-review',params=dict(QUERY,**query)).status_code==422


def test_scope_and_period(setup):
    app,client,base,evidence,facility,batch=setup
    expense(client,base,facility,evidence)
    assert client.get(base+'/cost-review',params=dict(start='2026-04',end='2026-04')).json()['rows']==[]
    assert client.get('/api/org/2/sorting/cost-review',params=QUERY).json()['rows']==[]
    assert client.get('/api/org/2/sorting/cost-review',params=dict(QUERY,facility_id=facility)).status_code==404
    assert client.get(base+'/cost-review',params=QUERY,headers={'x-epms-token':'invalid'}).status_code==403
