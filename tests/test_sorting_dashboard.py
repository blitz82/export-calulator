from decimal import Decimal
import pytest
from epms import sorting as s
from test_sorting import setup
from test_sorting_production import intake,transform,parent


QUERY=dict(start='2026-03-01',end='2026-03-31',as_of='2026-04-01')


def record(client,base,kind,data,previous=None):
    response=client.post(base+'/records',json=dict(kind=kind,data=data,previous_id=previous))
    assert response.status_code==200,response.text
    return response.json()['id']


def contract(client,base,facility,evidence):
    return record(client,base,'CONTRACT',dict(facility_id=facility,evidence_id=evidence,product='가상품목',quantity='1000',start='2026-01-01',end='2026-12-31',parties='synthetic-parties',terms='synthetic terms'))


def settlement(contract,evidence,**updates):
    return dict(dict(contract_id=contract,evidence_id=evidence,producer_id='synthetic-producer',sales='100',sorting_deduction='0',other_deduction='0',fee='0',adjustment_add='0',adjustment_subtract='0',paid='0',due_date='2026-03-31',state='UNPAID'),**updates)


def test_period_facility_rework_denominator_and_provenance(setup):
    app,client,base,evidence,facility,batch=setup
    entity=intake(client,base,facility,evidence,'intake-1','100.000001')
    body=transform(facility,evidence,[parent('INTAKE',entity,'100.000001')],export_quantity='80.000001',domestic_quantity='10',reject_quantity='5',loss_quantity='2',inventory_quantity='3')
    first=client.post(base+'/transforms',json=body).json()
    assert client.post(base+'/transforms',json=transform(facility,evidence,[parent('BATCH',first['batch_id'],'20')],external_id='rework',export_quantity='15',domestic_quantity='5')).status_code==200
    before=client.get(base+'/records').json()
    result=client.get(base+'/review-dashboard',params=dict(QUERY,facility_id=facility))
    assert result.status_code==200,result.text
    value=result.json()
    assert value['production']['intake']=='100.000001'
    assert value['production']['input']=='120.000001'
    assert value['production']['export']=='95.000001'
    assert value['counts']['work']==2 and value['counts']['intake']==1
    assert value['production']['export_suitability']['denominator']=='120.000001'
    assert value['joint_sorting_rate'] is None and value['payable_amount'] is None
    assert not value['operational_approval']
    assert all(r['evidence_id']==evidence and len(r['record_hash'])==64 for r in value['sources'])
    assert client.get(base+'/records').json()==before
    empty=client.get(base+'/review-dashboard',params=dict(QUERY,start='2026-04-01',end='2026-04-30')).json()
    assert empty['production']['export_suitability']['ratio'] is None
    assert empty['counts']['work']==0


def test_latest_settlement_due_cohort_overdue_boundary_dispute_and_overpayment(setup):
    app,client,base,evidence,facility,batch=setup
    agreement=contract(client,base,facility,evidence)
    old=record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence,paid='10'))
    newest=record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence,paid='25',state='DISPUTED'),old)
    overpaid=record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence,paid='120'))
    value=client.get(base+'/review-dashboard',params=QUERY).json()
    assert value['counts']['settlement']==2 and value['counts']['overdue']==1 and value['counts']['disputed']==1
    assert value['settlement']['net']=='200' and value['settlement']['paid']=='145'
    assert value['settlement']['remaining']=='75' and value['settlement']['overpaid']=='20'
    assert {r['record_id'] for r in value['sources']}=={newest,overpaid}
    assert value['settlement']['states']==dict(DISPUTED=1,UNPAID=1)
    assert client.get(base+'/review-dashboard',params=dict(QUERY,as_of='2026-03-31')).json()['counts']['overdue']==0
    # A revision moves the latest record out of the due-date cohort; the old version must not return.
    record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence,due_date='2026-04-10',paid='25'),newest)
    changed=client.get(base+'/review-dashboard',params=QUERY).json()
    assert changed['counts']['settlement']==1 and changed['settlement']['overpaid']=='20'
    assert changed['source_set_hash']!=value['source_set_hash']


def test_large_derived_net_and_current_revisions_not_historical_snapshot(setup):
    app,client,base,evidence,facility,batch=setup
    agreement=contract(client,base,facility,evidence)
    data=settlement(agreement,evidence,sales='9999999999999.123456',adjustment_add='9999999999999.123456')
    record(client,base,'PRODUCER_SETTLEMENT',data)
    value=client.get(base+'/review-dashboard',params=QUERY).json()
    assert Decimal(value['settlement']['net'])==Decimal('19999999999998.246912')
    assert value['settlement']['remaining']=='19999999999998.246912'


@pytest.mark.parametrize('changes',[dict(start='2026-03-32'),dict(start='20260301'),dict(start='2026-04-01'),dict(as_of='invalid')])
def test_invalid_periods_rejected(setup,changes):
    app,client,base,*_=setup
    assert client.get(base+'/review-dashboard',params=dict(QUERY,**changes)).status_code==422


def test_org_facility_scope_and_unavailable_feature(setup,monkeypatch,tmp_path):
    app,client,base,evidence,facility,batch=setup
    assert client.get('/api/org/2/sorting/review-dashboard',params=dict(QUERY,facility_id=facility)).status_code==404
    assert client.get('/api/org/2/sorting/review-dashboard',params=QUERY).json()['sources']==[]
    assert client.get(base+'/review-dashboard',params=QUERY,headers={'x-epms-token':'invalid'}).status_code==403
    from epms.api import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('EPMS_SORTING_ENABLED','0')
    other=create_app(tmp_path/'disabled.db')
    with TestClient(other) as disabled:
        assert disabled.get(base+'/review-dashboard',params=QUERY,headers={'x-epms-token':other.state.token}).status_code==404
    other.state.db.dispose()


def test_facility_isolation_and_due_date_cohort_not_contract_period(setup):
    app,client,base,evidence,facility,batch=setup
    second=client.post(base+'/facilities',json=dict(name='다른 가상시설',product='가상품목',member_number='1234567834',designation='TEST-2',valid_from='2026-01-01',valid_to='2027-12-31',evidence_id=evidence)).json()['id']
    intake(client,base,facility,evidence,'first','10')
    intake(client,base,second,evidence,'second','20')
    agreement=contract(client,base,second,evidence)
    record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence))
    record(client,base,'PRODUCER_SETTLEMENT',settlement(agreement,evidence,due_date='2026-04-10'))
    first=client.get(base+'/review-dashboard',params=dict(QUERY,facility_id=facility)).json()
    other=client.get(base+'/review-dashboard',params=dict(QUERY,facility_id=second)).json()
    all_rows=client.get(base+'/review-dashboard',params=QUERY).json()
    assert first['production']['intake']=='10' and first['counts']['settlement']==0
    assert other['production']['intake']=='20' and other['counts']['settlement']==1
    assert all_rows['production']['intake']=='30' and all_rows['counts']['settlement']==1
    assert all(row['facility_id']==second for row in other['sources'])
    assert client.get(base+'/review-dashboard',params=QUERY).json()['source_set_hash']==all_rows['source_set_hash']
