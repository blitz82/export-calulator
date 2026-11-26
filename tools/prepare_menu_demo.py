"""Prepare screenshots only: never opens the supplied database for writing."""
import os, json, sqlite3, hashlib, io
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from fastapi.testclient import TestClient
from epms.api import create_app

ROOT=Path('output/menu-demo-20261125'); DB=ROOT/'demo.db'
os.environ['EPMS_SORTING_ENABLED']='1'
summary=json.loads((ROOT/'source-summary.json').read_text(encoding='utf8'))
assert hashlib.sha256(Path('C:/Users/user/Downloads/epms.db').read_bytes()).hexdigest()==summary['sha256']
assert not (ROOT/'scenario.json').exists(), 'Demo already prepared'
# Replace imported identities and documents in the COPY; retain the 42/418 row structure.
c=sqlite3.connect(DB)
c.execute('PRAGMA foreign_keys=OFF')
for t in ['audit_logs','submission_snapshots','calculation_runs','import_rows','import_files','ocr_profiles']:
    if c.execute('select 1 from sqlite_master where name=?',(t,)).fetchone():c.execute('delete from '+t)
c.execute('update organizations set name=?',('감귤 · 가상 시연',))
ids=[r[0] for r in c.execute('select id from memberships order by id')]
names=['가상 푸른감귤 영농조합','가상 한라감귤 생산조합','가상 햇살감귤 농업회사','가상 바다무역','가상 제주수출','가상 새봄트레이딩']
def num(i):
    s=f'99000{i:04d}'; w=[1,3,7,1,3,7,1,3,5]; total=sum(int(x)*v for x,v in zip(s,w))+int(s[8])*5//10
    return s+str((10-total%10)%10)
members=[]
c.execute('delete from companies')
for i,key in enumerate(ids):
    d=dict(name=names[i] if i<6 else f'가상 회원사 {i+1:02d}',number=num(i+1),kind='생산자' if i<3 or i>=6 and i%2==0 else '수출업체',applied='2024-01-01',approved='2024-01-01',withdrawn='',aliases='',address='가상주소 제주특별자치도 시연로 100',representative=f'가상대표 {i+1:02d}',contact='가상 담당자',phone='',email='',corporate='',note='화면 시연 전용 가상 회원사')
    members.append(dict(d,id=key));c.execute('insert into companies values (?)',(d['number'],));c.execute('update memberships set number=?,revision=1,data=? where id=?',(d['number'],json.dumps(d,ensure_ascii=False),key))
pairs=[(0,3),(0,4),(1,3),(1,5),(2,4),(2,5)]
for i,(key,raw) in enumerate(c.execute('select id,data from declaration_lines').fetchall()):
    old=json.loads(raw); y=2025 if i<209 else 2026;p,e=pairs[i%6]
    d={k:str(old.get(k) or '') for k in ['usd','kg','quantity','unit']}
    d.update(number=f'99001-{str(y)[2:]}-{i+1:06d}',line='001',version='1',declared=f'{y}-10-{i%24+1:02d}',shipped=f'{y}-10-{i%24+2:02d}',accepted='',country='JP' if i%2==0 else 'SG',hs='0805211000' if i%2==0 else '0805219000',product='가상 감귤 수출',fob='FOB',scope='대상',status='유효')
    if not d['kg'] or d['kg']=='0':d['kg']=str(1000+i*10)
    if not d['usd']:d['usd']=str(3000+i*20)
    for role,m in [('maker',members[p]),('owner',members[e]),('agent',members[e])]:d.update({role:m['number'],role+'_name':m['name'],role+'_state':'회원 대조'})
    c.execute('update declaration_lines set revision=1,source=NULL,deleted=0,data=? where id=?',(json.dumps(d,ensure_ascii=False),key))
c.commit();c.close()
pdfmetrics.registerFont(TTFont('DemoKorean','C:/Windows/Fonts/malgun.ttf'))
def pdf(title,lines):
    b=io.BytesIO();p=canvas.Canvas(b,pagesize=(595,842));p.setFont('DemoKorean',19);p.drawString(45,790,title);p.setFont('DemoKorean',10)
    for i,line in enumerate(['가상 데이터 / 업무 흐름 시연 전용 / 실제 거래·지급 증빙 아님','']+lines):p.drawString(45,752-i*25,line)
    p.setFont('DemoKorean',9);p.drawString(45,55,'제공 DB의 작업용 사본에 생성한 가상 증빙 · 1 / 1');p.save();return b.getvalue()
app=create_app(DB);client=TestClient(app);client.headers['x-epms-token']=client.get('/api/session').json()['token']
def call(method,url,**kwargs):
    r=client.request(method,url,**kwargs)
    if not r.is_success:raise RuntimeError(f'{method} {url}: {r.status_code} {r.text}')
    return r.json()
prefix='/api/org/1'; sp=prefix+'/settlements'
for y in [2024,2025,2026]:call('PUT',prefix+f'/policies/{y}',json=dict(cutoff=f'{y}-12-31',basis='shipped',hs=['0805211000','0805219000'],reason='가상 화면 시연용 정책'))
contracts=[]
for i,(p,e) in enumerate(pairs):
    title=f'2026 감귤 공급·수출 약정 {i+1}'
    content=pdf(title,[f'생산자회원사: {members[p]["name"]}',f'수출업체: {members[e]["name"]}','체결일: 2025년 1월 1일 / 기간: 2025.01.01 ~ 2026.12.31','품목: 감귤 / HS 0805211000, 0805219000','공급: 선별·검수한 감귤을 수출용으로 공급한다.','수출: 수출업체가 신고 및 선적 업무를 수행한다.','대금: 선적 후 30일 이내 지급한다.','공동정산: 실적과 선별 증빙을 대조하여 별도 정산한다.'])
    doc=call('POST',prefix+'/contracts/documents/upload?name='+quote(f'가상_공급수출계약_{i+1}.pdf'),content=content)['id']
    contracts.append(call('POST',prefix+'/contracts',json=dict(title=title,kind='공급·수출·공동정산',producer_id=members[p]['id'],exporter_id=members[e]['id'],document_id=doc,signed='2025-01-01',start='2025-01-01',end='2026-12-31',period_type='기간 지정',supply='선별·검수한 감귤을 수출용으로 공급',export='신고·선적은 수출업체 담당',payment='선적 후 30일 이내 지급',settlement='실적과 증빙 대조 후 정산',costs='선별비는 별도 협의',review='확인 완료',evidence_pages={'parties':1,'payment':1},reason='가상 업무 시연')))
def upload(name,kind,lines):return call('POST',sp+'/documents/upload?name='+quote(name)+'&kind='+quote(kind),content=pdf(name[:-4],lines))['id']
designation=upload('가상_공동선별장_지정서.pdf','시설지정',['시설명: 가상 푸른감귤 공동선별장','운영자: 가상 푸른감귤 영농조합','지정번호: 가상-2026-001','유효기간: 2025.01.01 ~ 2027.12.31'])
facility=call('POST',prefix+'/sorting/facilities',json=dict(name='가상 푸른감귤 공동선별장',product='감귤 · 가상 시연',member_number=members[0]['number'],designation='가상-2026-001',operator=members[0]['name'],address='가상주소 제주특별자치도 시연로 100',valid_from='2025-01-01',valid_to='2027-12-31',evidence_id=designation))['id']
source_doc=upload('가상_11월_선별기록.pdf','선별일지',['생산자: 가상 푸른감귤 영농조합','11월 2일 / 총 1,500 kg / 수출 1,200 kg / 내수 200 kg / 탈락 100 kg','11월 3일 / 총 1,200 kg / 수출 900 kg / 내수 250 kg / 탈락 50 kg','11월 4일 / 총 1,000 kg / 수출 800 kg / 내수 150 kg / 탈락 50 kg','계근 차이: 선별 600 kg 대비 신고 순중량 590 kg (10 kg 차이)','차이 사유: 포장 전후 재계근으로 원문 확인 / 가상 시연'])
pay_doc=upload('가상_11월_급여지급기록.pdf','급여지급',['작업자: 가상 작업자 A, B, C (실존 인물 아님)','작업기간: 2026.11.02 ~ 2026.11.04','선별 작업 급여 발생액: 900,000 원','계좌이체 지급: 2026.11.10 / 900,000 원','내수 작업 제외액: 100,000 원','수출 선별 관련 확인액: 800,000 원'])
batches=[]
for i,(qty,total,domestic,rejected) in enumerate([('1200','1500','200','100'),('900','1200','250','50'),('800','1000','150','50')]):
    day=f'2026-11-{i+2:02d}'
    batches.append(call('POST',sp+'/sources',json=dict(facility_id=facility,title=f'11월 {i+2}일 수출 선별기록',external_id=f'DEMO-NOV-{i+2:02d}',period_type='일별',start=day,end=day,producer_id=members[0]['id'],export_quantity=qty,total=total,domestic=domestic,rejected=rejected,unit='kg',evidence=[{'file':source_doc,'page':1}],confirmed=True)))
expense=call('POST',sp+'/costs',json=dict(facility_id=facility,title='11월 선별작업 급여 지급',external_id='DEMO-PAY-NOV',start='2026-11-02',end='2026-11-04',category='선별 인건비',gross='900000',payments=[dict(reference='가상이체-20261110',date='2026-11-10',amount='900000')],excluded='100000',exclusion_reason='내수 작업분 제외',evidence=[{'file':pay_doc,'page':1}],batch_ids=[b['id'] for b in batches],confirmed=True))
decs=[]
for i,(kg,ex) in enumerate([('1000',3),('890',4),('800',3)]):
    d=dict(number=f'99099-26-00000{i+1}',line='001',version='1',declared='2026-11-05',shipped='2026-11-06',accepted='',country='JP',hs='0805211000',product='가상 감귤',usd=str(int(kg)*3),fob='FOB',kg=kg,quantity=kg,unit='KG',scope='대상',status='유효')
    for role,m in [('maker',members[0]),('owner',members[ex]),('agent',members[ex])]:d.update({role:m['number'],role+'_name':m['name'],role+'_state':'회원 대조'})
    row=call('POST',prefix+'/data/lines',json=[dict(values=d)])[0]['row'];decs.append(row)
    content=pdf(f'수출신고필증 (가상) {i+1}',[f'신고번호: {d["number"]} / 품목란: 001',f'제조자: {members[0]["name"]}',f'수출화주·대행사: {members[ex]["name"]}',f'품목: 감귤 / 순중량: {kg} kg',f'금액: USD {d["usd"]} / 목적국: 일본','선적일: 2026.11.06 / 신고일: 2026.11.05'])
    # Attach the synthetic PDF as the same native provenance used by OCR imports.
    with app.state.db.begin() as conn:
        from epms import store
        file=conn.execute(store.import_files.insert().values(org=1,sha=hashlib.sha256(content).hexdigest(),kind='pdf',name=f'가상_수출신고필증_{i+1}.pdf',data=content)).inserted_primary_key[0]
        src=conn.execute(store.import_rows.insert().values(file=file,sheet='PDF',row=1,original=d)).inserted_primary_key[0]
        conn.execute(store.declarations.update().where(store.declarations.c.id==row['id']).values(source=src))
edges=[]
for a,b,left,right in [(0,0,'1000','1000'),(0,1,'200','200'),(1,1,'700','690'),(2,2,'600','600')]:
    edges.append(dict(batch_id=batches[a]['id'],batch_revision=1,declaration_id=decs[b]['id'],declaration_revision=1,left=left,right=right,reason='포장 전후 재계근 차이 10 kg를 원문 대조' if left!=right else '',evidence=[{'file':source_doc,'page':1}] if left!=right else []))
job=dict(request_key='demo-create-'+uuid4().hex,title='가상 11월 공동선별비 정산',year=2026,facility_id=facility,start='2026-11-01',end='2026-11-30',policy=dict(label='가정값에 따른 참고 계산',unit_price='200',rate='50',tolerance_kg='10',assumptions_confirmed=True),lines=edges,costs=[dict(expense_id=expense['id'],revision=1,method='직접 확인액',amount='800000',reason='급여지급기록 중 수출 선별작업분 확인')],memo='정식 단가·보조율 미정 / 200원·50%는 시연 가정값')
saved=call('POST',sp,json=job);job.update(revision=saved['revision'],request_key='demo-confirm-'+uuid4().hex)
confirmed=call('POST',sp+f'/{saved["id"]}/confirm',json=job)
pdf_result=client.get(sp+f'/results/{confirmed["id"]}/pdf');pdf_result.raise_for_status();(ROOT/'가상_공동선별비_참고계산.pdf').write_bytes(pdf_result.content)
review=call('POST',prefix+'/submission-review?year=2026')
scenario=dict(members=[{k:m[k] for k in ['id','name','number']} for m in members],contracts=[x['id'] for x in contracts],facility=facility,batches=[x['id'] for x in batches],declarations=[x['id'] for x in decs],job=saved['id'],confirmation=confirmed,review=review)
(ROOT/'scenario.json').write_text(json.dumps(scenario,ensure_ascii=False,indent=2),encoding='utf8')
assert hashlib.sha256(Path('C:/Users/user/Downloads/epms.db').read_bytes()).hexdigest()==summary['sha256']
print(json.dumps(dict(members=len(members),historical_lines=418,new_lines=3,contracts=len(contracts),job=saved['id'],result=confirmed['snapshot']['result']),ensure_ascii=False))
client.close();app.state.db.dispose()
