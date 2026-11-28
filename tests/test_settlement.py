from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from epms import store,sorting as s,settlement as st,ocr,rules
from epms.api import create_app
from test_ocr import searchable_pdf

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('EPMS_SORTING_ENABLED','1')
    app=create_app(tmp_path/'settlement.db');c=TestClient(app)
    c.headers['x-epms-token']=c.get('/api/session').json()['token']
    c.post('/api/organizations?name=사과').raise_for_status()
    def number(seed):return next(seed+str(i) for i in range(10) if rules.valid_number(seed+str(i)))
    members=[]
    for i in range(1,4):
        data=dict(name=['생산조합','수출회사','다른 생산조합'][i-1],number=number(f'12345678{i}'),kind='수출업체' if i==2 else '생산자',approved='2024-01-01')
        members.append(c.post('/api/org/1/data/members',json=[dict(values=data)]).json()[0]['row'])
    path='/api/org/1/settlements';old='/api/org/1/sorting'
    doc=c.post(path+'/documents/upload?name=선별증빙.pdf&kind=선별일지',content=searchable_pdf()).raise_for_status().json()['id']
    facility=c.post(old+'/facilities',json=dict(name='공동선별장',product='사과',member_number=members[0]['number'],designation='테스트지정',valid_from='2025-01-01',valid_to='2027-12-31',evidence_id=doc)).raise_for_status().json()['id']
    c.post('/api/org/1/contracts',json=dict(title='수출 계약',kind='공급',review='확인 완료',evidence_pages={'parties':1},producer_id=1,exporter_id=2,document_id=c.post('/api/org/1/contracts/documents/upload?name=계약.pdf',content=searchable_pdf()).json()['id'],signed='2025-01-01',start='2025-01-01',end='2027-12-31',reason='합성 검증')).raise_for_status()
    yield c,app,path,doc,facility,members,tmp_path
    c.close();app.state.db.dispose()

def source(env,q='1000',name='작업1',**changes):
    c,app,path,doc,f,m,tmp=env
    d=dict(facility_id=f,title=name,external_id=name,period_type='일별',start='2026-07-01',end='2026-07-01',producer_id=1,
        export_quantity=q,total='',domestic='',rejected='',loss='',unit='kg',evidence=[dict(file=doc,page=1)],confirmed=True,overlap_reason='별개 작업 차수의 물량임을 원문 대조')
    d.update(changes)
    r=c.post(path+'/sources',json=d).raise_for_status().json()
    return r['id'],d

def declaration(env,q='1000',line='001'):
    c,app,path,doc,f,m,tmp=env
    d=dict(number='12345-26-123456',line=line,version='1',declared='2026-07-02',shipped='2026-07-03',usd='100',kg=q,hs='0808100000',scope='대상',status='유효')
    for role,member in [('maker',m[0]),('owner',m[1]),('agent',m[1])]:d.update({role:member['number'],role+'_name':member['name'],role+'_state':'회원 대조'})
    return c.post('/api/org/1/data/lines',json=[dict(values=d)]).raise_for_status().json()[0]['row']['id']

def payload(env,edges,**changes):
    return dict(request_key='create-'+uuid4().hex,title='합성 공동선별 정산',year=2026,facility_id=env[4],start='2026-07-01',end='2026-07-31',
        policy=dict(unit_price='10',rate='50',assumptions_confirmed=True),lines=[dict(batch_id=b,batch_revision=1,declaration_id=d,declaration_revision=1,left=a,right=r) for b,d,a,r in edges],**changes)

def draft(env,data):return env[0].post(env[2],json=data).raise_for_status().json()
def confirm(env,row,data):return env[0].post(f"{env[2]}/{row['id']}/confirm",json=dict(data,revision=row['revision'],request_key='confirm-'+str(row['id'])))

@pytest.mark.parametrize('left,right,mapping',[
    (['1000'],['1000'],[(0,0,'1000')]),
    (['1000'],['600','400'],[(0,0,'600'),(0,1,'400')]),
    (['400','600'],['1000'],[(0,0,'400'),(1,0,'600')]),
    (['700','500'],['600','600'],[(0,0,'600'),(0,1,'100'),(1,1,'500')]),
])
def test_equal_one_many_and_many_many(env,left,right,mapping):
    bs=[source(env,q,f'작업{i}')[0] for i,q in enumerate(left)];ds=[declaration(env,q,str(i+1)) for i,q in enumerate(right)]
    data=payload(env,[(bs[a],ds[b],q,q) for a,b,q in mapping]);job=draft(env,data)
    r=confirm(env,job,data).raise_for_status().json()
    assert r['snapshot']['result']['amount']==str(sum(map(int,left))*5)
    catalog=env[0].get(env[2]+'/catalog').json()
    assert all(v['available']=='0' for v in catalog['batches']+catalog['declarations'])
    assert r['snapshot']['result']['left']==r['snapshot']['result']['right']
    pdf=env[0].get(f"{env[2]}/results/{r['id']}/pdf").raise_for_status()
    assert pdf.content.startswith(b'%PDF-') and pdf.headers['content-type']=='application/pdf'
    if len(mapping)==1:
        output=Path('output/pdf');output.mkdir(parents=True,exist_ok=True);(output/'공동선별비_합성검증_정산결과.pdf').write_bytes(pdf.content)

def test_partial_release_replay_and_shared_legacy_capacity(env):
    b,_=source(env);d=declaration(env,'800');data=payload(env,[(b,d,'800','800')]);job=draft(env,data)
    first=confirm(env,job,data).raise_for_status().json();second=confirm(env,job,data).raise_for_status().json();assert first['id']==second['id']
    c,app,path,doc,f,m,tmp=env
    assert c.get(path+'/catalog').json()['batches'][0]['available']=='200'
    cap=c.get('/api/org/1/sorting/capacity',params=dict(declaration_id=d,declaration_revision=1,batch_id=b)).json()
    assert cap['declaration_available']=='0' and cap['batch_available']=='200'
    # Release preserves the old downloadable PDF while returning both balances.
    before=c.get(f"{path}/results/{first['id']}/pdf").content
    c.post(f"{path}/{job['id']}/release",json=dict(revision=first['revision'],reason='재연결')).raise_for_status()
    assert c.get(path+'/catalog').json()['batches'][0]['available']=='1000'
    assert c.get(f"{path}/results/{first['id']}/pdf").content==before

def test_difference_requires_reason_and_separate_physical_consumption(env):
    b,_=source(env);d=declaration(env,'980');data=payload(env,[(b,d,'1000','980')]);job=draft(env,data)
    assert confirm(env,job,data).status_code==422
    data['lines'][0].update(reason='계근 차이',exception_reason='두 증빙 대조',evidence=[dict(file=env[3],page=1)])
    r=confirm(env,job,data).raise_for_status().json()['snapshot']['result']
    assert r['amount']=='4900' and r['difference']=='20'
    assert all(v['available']=='0' for v in env[0].get(env[2]+'/catalog').json()['batches'])

def test_tolerance_and_atomic_capacity_failure(env):
    b,_=source(env);d=declaration(env,'1000');data=payload(env,[(b,d,'1000','980')]);data['lines'][0]['reason']='소량 차이';data['policy']['tolerance_kg']='20'
    job=draft(env,data);confirm(env,job,data).raise_for_status()
    d2=declaration(env,'1000','002');bad=payload(env,[(b,d2,'1','1')]);j2=draft(env,bad)
    assert confirm(env,j2,bad).status_code==422
    assert env[0].get(env[2]+'/catalog').json()['declarations'][1]['available']=='1000'

def test_decimal_tonnes_missing_unknown_mixed_and_overlap(env):
    b,_=source(env,'1.001',unit='t',name='톤 기록');d=declaration(env,'1001')
    data=payload(env,[(b,d,'1001','1001')]);data['policy']['unit_price']=''
    job=draft(env,data);r=env[0].post(f"{env[2]}/{job['id']}/preview",json=dict(data,revision=job['revision'])).json()
    assert r['amount'] is None and r['missing']
    b2,_=source(env,'100',name='겹치는 월별',period_type='월별',end='2026-07-31',overlap_reason='')
    data2=payload(env,[(b,d,'900','900'),(b2,d,'100','100')]);j=draft(env,data2)
    assert confirm(env,j,data2).status_code==422
    b3,_=source(env,'1',name='혼합',producer_id=None);d3=declaration(env,'1','003')
    data3=payload(env,[(b3,d3,'1','1')]);j3=draft(env,data3);assert confirm(env,j3,data3).status_code==422

def cost(env,b,gross='10000'):
    d=dict(facility_id=env[4],title='월급여',external_id='급여사건1',start='2026-07-01',end='2026-07-31',gross=gross,
        payments=[dict(reference='급여이체1',date='2026-07-31',amount=gross,refund=False)],excluded='0',evidence=[dict(file=env[3],page=1)],batch_ids=[b],confirmed=True)
    r=env[0].post(env[2]+'/costs',json=d).raise_for_status().json();return r,d

def test_cost_once_paid_refund_ratio_and_global_cost_capacity(env):
    b,_=source(env);d=declaration(env);c,original=cost(env,b)
    assert env[0].post(env[2]+'/costs',json=original).status_code==409
    data=payload(env,[(b,d,'500','500')]);data['policy']['formula']='실비 한도';data['costs']=[dict(expense_id=c['id'],revision=1,method='작업시간 비율',amount='',numerator='1',denominator='2',reason='수출 작업시간 50%')]
    job=draft(env,data);r=confirm(env,job,data).raise_for_status().json()['snapshot']['result']
    assert r['allocated_cost']=='5000.000000' and r['amount']=='2500'
    catalog=env[0].get(env[2]+'/catalog').json();assert catalog['expenses'][0]['available']=='5000.000000'
    second=deepcopy(data);second['request_key']='second-cost-job';second['costs'][0].update(method='수동 배부액',amount='5001')
    j2=draft(env,second);assert confirm(env,j2,second).status_code==422
    original['revision']=1;original['payments'].append(dict(reference='환입1',date='2026-08-01',amount='1000',refund=True))
    env[0].put(env[2]+f"/costs/{c['id']}",json=original).raise_for_status()
    assert env[0].get(env[2]+'/catalog').json()['expenses'][0]['available']=='4000.000000'

def test_race_source_revision_and_backup_restore(env):
    b,original=source(env);d=declaration(env)
    data=payload(env,[(b,d,'800','800')]);j1=draft(env,data);other=dict(data,request_key='other-job-create');j2=draft(env,other)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda j:confirm(env,j,data),[j1,j2]))
    assert sorted(r.status_code for r in responses)==[200,422]
    winner=next(r.json() for r in responses if r.status_code==200)
    backup=env[0].get('/api/backup').content;file=env[6]/'copy.db';file.write_bytes(backup)
    copy=create_app(file)
    try:
        with TestClient(copy) as c:
            c.headers['x-epms-token']=c.get('/api/session').json()['token']
            assert c.get(f"{env[2]}/results/{winner['id']}/pdf").content.startswith(b'%PDF-')
            assert c.get(env[2]+'/catalog').json()['batches'][0]['available']=='200'
    finally:copy.state.db.dispose()
    original['revision']=1;original['memo']='추가 확인'
    env[0].put(env[2]+f'/sources/{b}',json=original).raise_for_status()
    loser=j1 if responses[0].status_code==422 else j2
    assert confirm(env,loser,data).status_code==422

def test_generic_ocr_duplicate_pdf_and_scope(env):
    c,app,path,doc,f,m,tmp=env
    assert c.post(path+'/documents/upload?name=복사.pdf&kind=급여',content=searchable_pdf()).json()['duplicate']
    text=c.post(path+f'/documents/{doc}/ocr/1').raise_for_status().json()
    assert 'DECL-12345' in text['text'] and text['confirmed'] is False
    assert c.post(path+f'/documents/{doc}/ocr/2').status_code==422
    with app.state.db.begin() as conn:conn.execute(store.organizations.insert().values(id=2,name='다른 품목'))
    assert c.get(f'/api/org/2/settlements/documents/{doc}/page/1').status_code==404

def test_mixed_parent_pool_prevents_split_double_use(env):
    root,_=source(env,'1000',name='월별 혼합 원집계',producer_id=None,period_type='월별',end='2026-07-31')
    child1,_=source(env,'600',name='생산자 분할1',parent_id=root)
    child2,_=source(env,'600',name='생산자 분할2',parent_id=root)
    d1=declaration(env,'600','001');d2=declaration(env,'600','002')
    data=payload(env,[(child1,d1,'600','600')]);job=draft(env,data);confirm(env,job,data).raise_for_status()
    catalog=env[0].get(env[2]+'/catalog').json()
    assert next(b for b in catalog['batches'] if b['id']==child2)['available']=='400'
    other=payload(env,[(child2,d2,'600','600')]);j2=draft(env,other)
    assert confirm(env,j2,other).status_code==422

def test_policies_caps_invalid_source_and_cost_reuse(env):
    b,_=source(env,'1000');d=declaration(env,'1000')
    data=payload(env,[(b,d,'1000','1000')]);data['policy'].update(cap='3000',cap_quantity='900',deduction='100',scope_cap='2500')
    job=draft(env,data);r=confirm(env,job,data).raise_for_status().json()['snapshot']['result'];assert r['amount']=='2500' and r['billable']=='900'
    # A copied bank transaction in a separate cost event is rejected.
    original,_=cost(env,b)
    copy_data=dict(facility_id=env[4],title='다른 파일의 같은 지급',external_id='다른 비용번호',start='2026-07-01',end='2026-07-31',gross='10000',payments=[dict(reference='급여이체1',date='2026-07-31',amount='10000')],evidence=[dict(file=env[3],page=1)],batch_ids=[b])
    assert env[0].post(env[2]+'/costs',json=copy_data).status_code==409

def test_missing_contract_bad_pages_and_no_confirmation_evasion(env):
    b,_=source(env,'1000');d=declaration(env,'1000')
    data=payload(env,[(b,d,'1000','1000')]);job=draft(env,data)
    with env[1].state.db.begin() as conn:
        from epms import member_contracts as mc
        row=conn.execute(mc.contracts.select()).mappings().first();change=dict(row['data'],end='2026-06-30')
        conn.execute(mc.contracts.update().where(mc.contracts.c.id==row['id']).values(data=change,revision=2))
    assert confirm(env,job,data).status_code==422

def test_multi_page_korean_pdf_and_last_row_preserved(env):
    from pypdf import PdfReader
    from io import BytesIO
    edges=[]
    for i in range(15):
        b,_=source(env,'10',name=f'생산자별 공동선별기록 {i+1} '+('긴 한글 업체명 확인 '*5))
        d=declaration(env,'10',str(i+1));edges.append((b,d,'10','10'))
    data=payload(env,edges);job=draft(env,data);r=confirm(env,job,data).raise_for_status().json()
    pdf=env[0].get(f"{env[2]}/results/{r['id']}/pdf").content
    reader=PdfReader(BytesIO(pdf));assert len(reader.pages)>1
    text='\n'.join(p.extract_text() for p in reader.pages)
    assert '생산자별 공동선별기록 15' in text and '공동선별비 정산 결과' in text
    Path('output/pdf/공동선별비_다중페이지_검증.pdf').write_bytes(pdf)

def test_scanned_ocr_failure_still_allows_manual_entry(env,monkeypatch):
    monkeypatch.setattr(ocr,'recognize',lambda image:({},'테스트 OCR 언어팩 미설치'))
    scan=searchable_pdf().replace(b'DECL-12345',b'          ')
    r=env[0].post(env[2]+'/documents/upload?name=스캔.pdf&kind=월별기록',content=scan).raise_for_status().json()
    extracted=env[0].post(env[2]+f"/documents/{r['id']}/ocr/1").raise_for_status().json()
    assert extracted['warning'] and not extracted['candidates']
    b,_=source(env,'10',name='수동 확인',evidence=[dict(file=r['id'],page=1)])
    d=declaration(env,'10');data=payload(env,[(b,d,'10','10')]);job=draft(env,data)
    confirm(env,job,data).raise_for_status()

def test_difference_evidence_page_is_validated(env):
    b,_=source(env);d=declaration(env);data=payload(env,[(b,d,'1000','980')]);job=draft(env,data)
    data['lines'][0].update(reason='계근',exception_reason='확인',evidence=[dict(file=env[3],page=2)])
    assert confirm(env,job,data).status_code==422
