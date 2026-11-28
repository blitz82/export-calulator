from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pypdf import PdfReader
from fastapi.testclient import TestClient
from epms.api import create_app
from epms import store,sorting as s,support_budget as budget
from test_settlement import env,source,declaration,payload,draft,confirm,cost

def setup(env,**changes):
    d=dict(title='2026 가정 지원기준',enabled=True,status='가정값',start='2026-01-01',end='2026-12-31',total='100000',unit_price='10',rate_included=False,rate='50',allocations=[dict(member_id=1,amount='50000'),dict(member_id=3,amount='50000')],reason='격리 검증')
    d.update(changes);r=env[0].put('/api/org/1/support-budgets/2026',json=d).raise_for_status().json();return r,d
def read(env):return env[0].get('/api/org/1/support-budgets/2026').raise_for_status().json()

def test_quota_central_rate_ack_and_pdf(env):
    plan,_=setup(env,allocations=[dict(member_id=1,amount='3000')]);b,_=source(env);decl=declaration(env)
    data=payload(env,[(b,decl,'1000','1000')]);data['policy']['unit_price']='999999';job=draft(env,data)
    assert confirm(env,job,data).status_code==422
    data['support_revision']=1;r=confirm(env,job,data).raise_for_status().json()
    assert r['snapshot']['result']['amount']=='3000' and r['snapshot']['result']['support']['unfunded']=='2000'
    assert read(env)['used']=='3000'
    assert r['snapshot']['result']['policy']['unit_price']=='10'
    cat=env[0].get(env[2]+'/catalog').json();assert cat['batches'][0]['available']=='0'
    text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(env[0].get(env[2]+f'/results/{r["id"]}/pdf').content)).pages)
    assert '회원사 배정' in text and '3000' in text

def test_release_reconfirm_delta_and_topup_preserve_pdf(env):
    p,config=setup(env,allocations=[dict(member_id=1,amount='3000')]);b,_=source(env);decl=declaration(env)
    data=payload(env,[(b,decl,'1000','1000')]);data['support_revision']=1;job=draft(env,data);first=confirm(env,job,data).raise_for_status().json()
    original=env[0].get(env[2]+f'/results/{first["id"]}/pdf').content
    assert confirm(env,job,data).json()['id']==first['id'] and read(env)['used']=='3000'
    env[0].put('/api/org/1/support-budgets/2026',json=dict(config,revision=1,allocations=[dict(member_id=1,amount='6000')],reason='추가 배정')).raise_for_status()
    revised=dict(data,revision=first['revision'],request_key='topup-request',support_revision=2)
    second=env[0].post(env[2]+f'/{job["id"]}/confirm',json=revised).raise_for_status().json()
    assert second['snapshot']['result']['amount']=='5000' and read(env)['used']=='5000'
    assert env[0].get(env[2]+f'/results/{first["id"]}/pdf').content==original
    env[0].post(env[2]+f'/{job["id"]}/release',json=dict(revision=second['revision'],reason='재검토')).raise_for_status()
    assert read(env)['used']=='0' and env[0].get(env[2]+'/catalog').json()['batches'][0]['available']=='1000'

def test_member_cap_spans_facilities_and_atomic_race(env):
    setup(env,allocations=[dict(member_id=1,amount='5000')]);b,_=source(env);decl=declaration(env)
    a=payload(env,[(b,decl,'400','400')]);a['support_revision']=1
    # Independent drafts both preview without reserving any budget.
    jobs=[draft(env,a),draft(env,dict(a,request_key='other-budget-job'))]
    assert read(env)['used']=='0'
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda job:confirm(env,job,a),jobs))
    assert all(r.status_code==200 for r in responses)
    assert read(env)['used']=='4000'
    remaining=payload(env,[(b,decl,'200','200')]);remaining['support_revision']=1
    job=draft(env,remaining);r=confirm(env,job,remaining).raise_for_status().json();assert r['snapshot']['result']['amount']=='1000'
    assert read(env)['remaining']=='95000'

def test_revision_allocation_total_used_reduction_and_missing_member(env):
    p,config=setup(env);b,_=source(env);decl=declaration(env);data=payload(env,[(b,decl,'1000','1000')]);data['support_revision']=1;job=draft(env,data)
    assert env[0].put('/api/org/1/support-budgets/2026',json=dict(config,revision=0)).status_code==409
    assert env[0].put('/api/org/1/support-budgets/2026',json=dict(config,revision=1,total='1')).status_code==422
    confirm(env,job,data).raise_for_status()
    assert env[0].put('/api/org/1/support-budgets/2026',json=dict(config,revision=1,allocations=[dict(member_id=1,amount='1000')])).status_code==422
    assert env[0].put('/api/org/1/support-budgets/2026',json=dict(config,revision=1,enabled=False)).status_code==422

def test_legacy_explicit_adoption_exclusion_and_remainder(env):
    b,_=source(env);decl=declaration(env);data=payload(env,[(b,decl,'200','200')]);old=draft(env,data);snapshot=confirm(env,old,data).raise_for_status().json()
    setup(env);assert read(env)['used']=='0' and len(read(env)['pending'])==1
    new=dict(data,request_key='new-budget-draft',support_revision=1);job=draft(env,new)
    assert confirm(env,job,new).status_code==422
    env[0].post(f'/api/org/1/support-budgets/2026/legacy/{old["id"]}',json=dict(snapshot_id=snapshot['id'],decision='반영',allocations=[dict(member_id=1,amount='1000')],reason='기존 확정액 대조')).raise_for_status()
    assert read(env)['used']=='1000' and not read(env)['pending']
    confirm(env,job,new).raise_for_status();assert read(env)['used']=='2000'
    assert budget.distribute(5,{1:budget.Decimal(1),3:budget.Decimal(1)})=={1:3,3:2}

def test_multi_member_cost_allocation_and_plan_period(env):
    _,config=setup(env,formula='실비 한도');b,_=source(env);decl=declaration(env);expense,_=cost(env,b)
    data=payload(env,[(b,decl,'1000','1000')]);data['support_revision']=1;data['costs']=[dict(expense_id=expense['id'],revision=1,method='직접 확인액',amount='10000',reason='직접 확인')]
    job=draft(env,data);assert confirm(env,job,data).status_code==422
    data['member_costs']=[dict(member_id=1,amount='10000')];confirm(env,job,data).raise_for_status()

def test_scope_backup_and_unassigned_member_cannot_confirm(env):
    setup(env,allocations=[]);b,_=source(env);decl=declaration(env);data=payload(env,[(b,decl,'10','10')]);data['support_revision']=1;job=draft(env,data)
    assert confirm(env,job,data).status_code==422
    with env[1].state.db.begin() as conn:conn.execute(store.organizations.insert().values(id=2,name='다른 조직'))
    other=env[0].get('/api/org/2/support-budgets/2026').json();assert other['plan'] is None and other['usage']==[] and other['pending']==[]
    p=env[6]/'support-backup.db';p.write_bytes(env[0].get('/api/backup').content);app=create_app(p)
    try:
        with TestClient(app) as client:
            client.headers['x-epms-token']=client.get('/api/session').json()['token'];assert client.get('/api/org/1/support-budgets/2026').json()['plan']['revision']==1
    finally:app.state.db.dispose()

def test_budget_follows_member_across_two_facilities(env):
    setup(env,allocations=[dict(member_id=1,amount='6000')])
    facility=env[0].post('/api/org/1/sorting/facilities',json=dict(name='다른 공동선별장',product='사과',member_number=env[5][0]['number'],designation='다른 지정',valid_from='2025-01-01',valid_to='2027-12-31',evidence_id=env[3])).raise_for_status().json()['id']
    for i,f in enumerate([env[4],facility]):
        b,_=source(env,name=f'시설{i}',facility_id=f);decl=declaration(env,line=f'{i+1:03d}')
        data=payload(env,[(b,decl,'1000','1000')]);data.update(facility_id=f,support_revision=1);job=draft(env,data)
        result=confirm(env,job,data).raise_for_status().json()['snapshot']['result'];assert result['amount']==('5000' if i==0 else '1000')
    assert read(env)['used']=='6000'

def test_multi_producer_costs_are_not_reused_for_each_member(env):
    setup(env,formula='실비 한도');c=env[0]
    contract=c.get('/api/org/1/contracts').json()[0]
    c.post('/api/org/1/contracts',json=dict(title='다른 생산자 계약',kind='공급',review='확인 완료',evidence_pages={'parties':1},producer_id=3,exporter_id=2,document_id=contract['document_id'],signed='2025-01-01',start='2025-01-01',end='2027-12-31',reason='격리 검증')).raise_for_status()
    a,_=source(env,'600',name='생산자1');b,_=source(env,'400',name='생산자3',producer_id=3);d1=declaration(env,'600');d2=declaration(env,'400','002')
    line=c.get('/api/org/1/data/lines').json()[-1]
    c.post('/api/org/1/data/lines',json=[dict(id=d2,revision=line['revision'],reason='생산자 대조',values={k:str(v) for k,v in dict(line,maker=env[5][2]['number'],maker_name=env[5][2]['name']).items() if k not in {'id','revision','source','pdf'}})]).raise_for_status()
    expense,_=cost(env,a);data=payload(env,[(a,d1,'600','600'),(b,d2,'400','400')]);data['lines'][1]['declaration_revision']=2;data['support_revision']=1
    data['costs']=[dict(expense_id=expense['id'],revision=1,method='직접 확인액',amount='10000',reason='직접 확인')];data['member_costs']=[dict(member_id=1,amount='1000'),dict(member_id=3,amount='9000')]
    job=draft(env,data);result=confirm(env,job,data).raise_for_status().json()['snapshot']['result']
    assert result['amount']=='2500' and [m['amount'] for m in result['support']['members']]==['500','2000']
