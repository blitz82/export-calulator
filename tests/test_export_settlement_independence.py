"""Export attribution must not depend on sorting-support consumption or payment."""
from io import BytesIO
from uuid import uuid4
from copy import deepcopy
import pytest
from openpyxl import load_workbook
from test_settlement import env,source,declaration,payload,draft,confirm
from test_support_budget import setup as budget_setup
from test_monthly_report import create as report_create,freeze as report_freeze

def policies(env):
    c=env[0]
    for year in [2025,2026]:c.put(f'/api/org/1/policies/{year}',json=dict(cutoff=f'{year}-12-31',basis='shipped',hs=['0808100000'],reason='격리된 독립성 검증')).raise_for_status()

def annual(env):
    c=env[0];ids=[c.post(f'/api/org/1/runs?year={year}').raise_for_status().json()['id'] for year in [2025,2026]]
    r=c.get(f'/api/org/1/runs/{ids[1]}').raise_for_status().json();assert r['ready']
    x=c.post('/api/org/1/annual-submission',params=dict(previous_id=ids[0],run_id=ids[1],final=True,require_current=True)).raise_for_status()
    wb=load_workbook(BytesIO(x.content),data_only=False)
    try:business={ws.title:list(ws.values) for ws in wb if ws.title!='계산 및 검증 정보'}
    finally:wb.close()
    return dict(result=r,business=business,ids=ids,excel=x.content)

def inputs(env):
    c=env[0]
    return dict(lines=c.get('/api/org/1/data/lines').json(),members=c.get('/api/org/1/data/members').json(),policies=c.get('/api/org/1/policies').json())

def stable(env,baseline,original):
    current=annual(env)
    assert current['result']==baseline['result']
    assert current['business']==baseline['business']
    assert inputs(env)==original
    old=env[0].post('/api/org/1/annual-submission',params=dict(previous_id=baseline['ids'][0],run_id=baseline['ids'][1],final=True,require_current=True)).raise_for_status()
    assert old.content==baseline['excel']
    return current

def test_unmatched_partial_full_and_released_support_do_not_change_exports(env):
    c=env[0];policies(env);one=declaration(env,'1000');two=declaration(env,'500','002')
    baseline=annual(env);original=inputs(env)
    assert baseline['result']['actual_usd']=='200' and baseline['result']['actual_kg']=='1500'
    summary={m['number']:m for m in baseline['result']['summary']}
    assert str(summary[env[5][0]['number']]['indirect_usd'])=='200'
    assert str(summary[env[5][1]['number']]['direct_usd'])=='200'
    # No sorting source or matching job is needed to produce final export output.
    assert c.get(env[2]+'/catalog').json()['batches']==[]
    b,_=source(env);stable(env,baseline,original)
    data=payload(env,[(b,one,'400','400')]);job=draft(env,data);stable(env,baseline,original)
    first=confirm(env,job,data).raise_for_status().json()
    available={d['id']:d['available'] for d in c.get(env[2]+'/catalog').json()['declarations']}
    assert available=={one:'600',two:'500'};stable(env,baseline,original)
    data['lines'][0].update(left='1000',right='1000')
    full=c.post(env[2]+f"/{job['id']}/confirm",json=dict(data,revision=first['revision'],request_key=uuid4().hex)).raise_for_status().json()
    assert full['snapshot']['result']['amount']=='5000';stable(env,baseline,original)
    c.post(env[2]+f"/{job['id']}/release",json=dict(revision=full['revision'],reason='격리된 연결 해제 독립성 검증')).raise_for_status()
    stable(env,baseline,original)
    assert {d['id']:d['available'] for d in c.get(env[2]+'/catalog').json()['declarations']}=={one:'1000',two:'500'}

@pytest.mark.parametrize('failure',['unreviewed_source','missing_member_budget'])
def test_support_validation_failure_does_not_block_export_confirmation(env,failure):
    c=env[0];policies(env);d=declaration(env);baseline=annual(env);original=inputs(env)
    b,_=source(env,confirmed=failure!='unreviewed_source');data=payload(env,[(b,d,'1000','1000')])
    if failure=='missing_member_budget':budget_setup(env,allocations=[]);data['support_revision']=1
    job=draft(env,data);failed=confirm(env,job,data)
    assert failed.status_code==422
    stable(env,baseline,original)
    assert not any(p['severity']!='INFO' for p in c.get('/api/org/1/line-checks').json()[str(d)])

def test_support_caps_policy_changes_monthly_freeze_and_payments_are_independent(env):
    c=env[0];policies(env);plan,criteria=budget_setup(env,allocations=[dict(member_id=1,amount='3000')]);d=declaration(env);declaration(env,'500','002')
    baseline=annual(env);original=inputs(env);b,_=source(env);data=payload(env,[(b,d,'1000','1000')]);data['support_revision']=plan['revision'];job=draft(env,data)
    snap=confirm(env,job,data).raise_for_status().json();assert snap['snapshot']['result']['amount']=='3000'
    stable(env,baseline,original)
    ref=dict(file=env[3],page=1)
    report=dict(title='가상 월별 지급 독립성',year=2026,month=7,start='2026-07-01',end='2026-07-31',destination='aT 가상 지역본부',writer='가상 사무국',reason='격리된 독립성 검증',reference_ack=True,personal_check=True,selections=[dict(snapshot_id=snap['id'])],declaration_evidence=[dict(declaration_id=d,**ref)],reviews=[dict(member_id=1,decision='2900',date='2026-08-01',reviewer='가상 검토자',difference_reason='검토 조정 100원 및 결의 보류 100원',unpaid_reason='추가 지급 예정 300원',evidence=[ref])],resolutions=[dict(key='resolution',number='가상-결의',date='2026-08-02',total='2800',scope_amount='2800',allocations=[dict(member_id=1,amount='2800')],evidence=[ref])],payments=[dict(key='paid',reference='가상-이체',date='2026-08-03',recipient=env[5][0]['name'],amount='2500',allocations=[dict(member_id=1,amount='2500')],evidence=[ref])])
    row=report_create(env,report);frozen=report_freeze(env,row).raise_for_status().json();stable(env,baseline,original)
    c.post(f"/api/org/1/monthly-reports/{row['id']}/submitted",json=dict(revision=frozen['revision'],reason='가상 제출 기록',date='2026-08-04',destination='aT 가상 지역본부',method='가상 기록')).raise_for_status()
    stable(env,baseline,original)
    # Support unit rates, member allocations and annual caps live in separate data.
    changed=deepcopy(criteria);changed.update(revision=plan['revision'],unit_price='20',total='200000',allocations=[dict(member_id=1,amount='5000')],reason='지원기준만 변경')
    c.put('/api/org/1/support-budgets/2026',json=changed).raise_for_status();stable(env,baseline,original)
