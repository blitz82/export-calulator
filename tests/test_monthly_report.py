from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
import sqlite3
import pytest
from pypdf import PdfReader
from test_settlement import env,source,declaration,payload,draft,confirm

def report_data(env,amount='5000'):
    b,_=source(env);dec=declaration(env);data=payload(env,[(b,dec,'1000','1000')]);job=draft(env,data)
    snapshot=confirm(env,job,data).raise_for_status().json()
    return dict(title='7월 공동선별비 보고',year=2026,month=7,start='2026-07-01',end='2026-07-31',destination='aT 테스트 지역본부',writer='가상 사무국',reason='격리 검증',reference_ack=True,personal_check=True,
        selections=[dict(snapshot_id=snapshot['id'])],declaration_evidence=[dict(declaration_id=dec,file=env[3],page=1)],
        reviews=[dict(member_id=1,decision=amount,date='2026-08-01',reviewer='검토자',evidence=[dict(file=env[3],page=1)])],
        resolutions=[dict(key='resolution-1',number='지결-7월-1',date='2026-08-02',total=amount,scope_amount=amount,allocations=[dict(member_id=1,amount=amount)],evidence=[dict(file=env[3],page=1)])],
        payments=[dict(key='pay-1',reference='거래번호-1',date='2026-08-03',recipient='생산조합',amount=amount,allocations=[dict(member_id=1,amount=amount)],evidence=[dict(file=env[3],page=1)])]),snapshot,job,data

def create(env,data):return env[0].post('/api/org/1/monthly-reports',json=data).raise_for_status().json()
def freeze(env,row):return env[0].post(f"/api/org/1/monthly-reports/{row['id']}/freeze",json=dict(revision=row['revision'],reason='생성 검증'))

def test_monthly_report_pdf_archive_and_locks(env):
    data,snap,job,original=report_data(env);c=env[0];row=create(env,data);root=f"/api/org/1/monthly-reports/{row['id']}"
    p=c.get(root+'/preview').raise_for_status().json();assert p['errors']==[];assert p['totals']['net']=='5000'
    package=freeze(env,row).raise_for_status().json();pdf=c.get(root+'/pdf').raise_for_status().content
    reader=PdfReader(BytesIO(pdf));assert len(reader.pages)>=5;assert len(reader.outline)>=2
    assert '생산조합' in ''.join(page.extract_text() for page in reader.pages)
    assert any('/Annots' in page for page in reader.pages)
    archive=ZipFile(BytesIO(c.get(root+'/pdf?originals=true').content));assert '제출자료_목록.json' in archive.namelist()
    assert sha256(pdf).hexdigest()==package['sha']
    assert len([n for n in archive.namelist() if n.startswith('원본/')])==1 # deduplicated by hash
    assert freeze(env,dict(row,revision=package['revision'])).json()['id']==package['id']
    assert c.put(env[2]+f"/{job['id']}",json=dict(original,revision=snap['revision'])).status_code==409
    assert c.post(env[2]+f"/{job['id']}/release",json=dict(revision=snap['revision'],reason='잠금 확인')).status_code==409
    c.post(root+'/submitted',json=dict(revision=package['revision'],reason='제출 기록',date='2026-08-05',destination='aT 테스트 지역본부',method='담당자 전달',receipt='가상 접수')).raise_for_status()
    assert c.get(root+'/pdf').content==pdf
    assert c.get(root+'/history').json()[0]['after']['receipt']=='가상 접수'
    (env[6]/'report.pdf').write_bytes(pdf)
    from pathlib import Path
    Path('output/pdf').mkdir(parents=True,exist_ok=True);Path('output/pdf/monthly-report-synthetic.pdf').write_bytes(pdf)

def test_incomplete_draft_overpayment_unpaid_and_reference(env):
    data,*_=report_data(env);c=env[0];data['payments']=[];data['personal_check']=False;data['reference_ack']=False;data['declaration_evidence']=[]
    row=create(env,data);p=c.get(f"/api/org/1/monthly-reports/{row['id']}/preview").json()
    assert any('미지급 사유' in e for e in p['errors']);assert any('개인정보' in e for e in p['errors']);assert any('원본 PDF' in e for e in p['errors']);assert freeze(env,row).status_code==422
    data['reviews'][0].update(unpaid_reason='다음 달 지급',expected_date='2026-09-01');data.update(personal_check=True,reference_ack=True,declaration_evidence=[dict(declaration_id=1,file=env[3],page=1)])
    updated=c.put(f"/api/org/1/monthly-reports/{row['id']}",json=dict(data,revision=row['revision'])).raise_for_status().json()
    assert freeze(env,updated).is_success

def test_difference_reasons_cannot_approve_excess(env):
    data,*_=report_data(env,amount='6000');data['reviews'][0]['difference_reason']='추가 지급 사유를 작성해도 초과는 해결되지 않음'
    row=create(env,data);assert freeze(env,row).status_code==422
    data['reviews'][0]['decision']='4500';data['resolutions'][0].update(total='4500',scope_amount='4500',allocations=[dict(member_id=1,amount='4500')]);data['payments'][0].update(amount='4500',allocations=[dict(member_id=1,amount='4500')])
    row=create(env,data);assert freeze(env,row).is_success

def test_duplicate_report_correction_and_immutable_original(env):
    data,snap,job,original=report_data(env);c=env[0];one=create(env,data);f=freeze(env,one).raise_for_status().json();pdf=c.get(f"/api/org/1/monthly-reports/{one['id']}/pdf").content
    duplicate=create(env,data);assert freeze(env,duplicate).status_code==422
    correction=create(env,dict(data,previous=one['id'],reason='보완 제출'))
    assert c.post('/api/org/1/monthly-reports',json=dict(data,previous=one['id'],reason='분기 충돌')).status_code==409
    frozen=freeze(env,correction).raise_for_status().json();assert frozen['id']!=f['id'];assert c.get(f"/api/org/1/monthly-reports/{one['id']}/pdf").content==pdf
    # Superseded packages cannot unlock the currently active correction.
    assert c.post(f"/api/org/1/monthly-reports/{one['id']}/unlock",json=dict(revision=f['revision'],reason='구버전 잠금 해제')).status_code==409
    c.post(f"/api/org/1/monthly-reports/{correction['id']}/unlock",json=dict(revision=frozen['revision'],reason='계산 정정 필요')).raise_for_status()
    assert c.post(env[2]+f"/{job['id']}/release",json=dict(revision=snap['revision'],reason='계산 정정')).is_success

def test_partial_reporting_and_payment_duplicate_capacity(env):
    data,*_=report_data(env);c=env[0]
    for amount,kg,month in [('2000','400',7),('3000','600',8)]:
        d=deepcopy(data);d['month']=month;d['selections'][0].update(portions=[dict(member_id=1,amount=amount,kg=kg)],reason='원문 기준 분할 보고')
        d['reviews'][0]['decision']=amount;d['resolutions'][0]['scope_amount']=amount;d['resolutions'][0]['allocations'][0]['amount']=amount;d['payments'][0]['allocations'][0]['amount']=amount
        row=create(env,d);freeze(env,row).raise_for_status()
    row=create(env,data);assert freeze(env,row).status_code==422

def test_scope_cas_backup_and_manual_page_validation(env):
    data,*_=report_data(env);c=env[0];row=create(env,data);root=f"/api/org/1/monthly-reports/{row['id']}"
    assert c.put(root,json=dict(data,revision=0)).status_code==409
    from epms import store
    with env[1].state.db.begin() as conn:conn.execute(store.organizations.insert().values(name='다른품목'))
    assert c.get(root.replace('/org/1/','/org/2/')+'/preview').status_code==404
    data['declaration_evidence'][0]['page']=999;assert c.put(root,json=dict(data,revision=row['revision'])).status_code==422
    freeze(env,row).raise_for_status();backup=c.get('/api/backup').raise_for_status().content;p=env[6]/'backup.db';p.write_bytes(backup)
    con=sqlite3.connect(p);assert con.execute('select count(*) from sorting_monthly_packages').fetchone()==(1,);con.close()

def test_shared_document_multi_member_legacy_attribution(env):
    data,snap,job,original=report_data(env);c=env[0]
    # A second producer is allowed to supply the same exporter under its own contract.
    from test_ocr import searchable_pdf
    contract=c.post('/api/org/1/contracts/documents/upload?name=다른생산자.pdf',content=searchable_pdf()).raise_for_status().json()['id']
    c.post('/api/org/1/contracts',json=dict(title='다른 생산자 계약',kind='공급',producer_id=3,exporter_id=2,document_id=contract,signed='2025-01-01',start='2025-01-01',end='2027-12-31',reason='격리 검증')).raise_for_status()
    other=(*env[:5],[env[5][2],env[5][1],env[5][0]],env[6]);b,_=source(other,'500','별개 생산자 작업',producer_id=3);d=declaration(other,'500','002')
    # Release and reconfirm one mixed-producer calculation, never altering an operating DB.
    c.post(env[2]+f"/{job['id']}/release",json=dict(revision=snap['revision'],reason='혼합 생산자 검증')).raise_for_status()
    original['lines'].append(dict(batch_id=b,batch_revision=1,declaration_id=d,declaration_revision=1,left='500',right='500'))
    current=c.get(env[2]).json()[0]
    snap=c.post(env[2]+f"/{job['id']}/confirm",json=dict(original,revision=current['revision'],request_key='mixed-monthly')).raise_for_status().json()
    data['selections']=[dict(snapshot_id=snap['id'],attribution=[dict(member_id=1,amount='5000'),dict(member_id=3,amount='2500')])]
    data['reviews'].append(dict(data['reviews'][0],member_id=3,decision='2500'))
    data['resolutions'][0].update(total='10000',scope_amount='7500',allocations=[dict(member_id=1,amount='5000'),dict(member_id=3,amount='2500')])
    data['payments'][0].update(amount='7500',recipient='일괄 이체 명세',recipient_reason='회원사별 이체 명세를 공통 증빙으로 대조',allocations=[dict(member_id=1,amount='5000'),dict(member_id=3,amount='2500')])
    data['declaration_evidence'].append(dict(declaration_id=d,file=env[3],page=1))
    row=create(env,data);freeze(env,row).raise_for_status()
    package=c.get(f"/api/org/1/monthly-reports/{row['id']}/package").json()['data']
    assert package['totals']['net']=='7500' and len(package['members'])==2

def test_refund_and_failure_rollback(env,monkeypatch):
    data,*_=report_data(env);c=env[0]
    refund=deepcopy(data['payments'][0]);refund.update(key='refund',refund=True,amount='1000',allocations=[dict(member_id=1,amount='1000')])
    data['payments'].append(refund);data['reviews'][0]['unpaid_reason']='반환 후 보완 대기'
    row=create(env,data)
    from epms import monthly_report_pdf as pdf
    from fastapi import HTTPException
    real=pdf.render
    def broken(*args):raise HTTPException(422,'원본 결합 실패 검증')
    monkeypatch.setattr(pdf,'render',broken);assert freeze(env,row).status_code==422
    assert c.get('/api/org/1/monthly-reports').json()[0]['state']=='DRAFT'
    from epms import monthly_report as m
    from sqlalchemy import select,func
    with env[1].state.db.connect() as conn:assert conn.execute(select(func.count()).select_from(m.claims)).scalar_one()==0
    monkeypatch.setattr(pdf,'render',real);freeze(env,row).raise_for_status()
    assert c.get(f"/api/org/1/monthly-reports/{row['id']}/package").json()['data']['totals']['net']=='4000'

def test_two_windows_cannot_double_report(env):
    from concurrent.futures import ThreadPoolExecutor
    data,*_=report_data(env);rows=[create(env,data),create(env,data)]
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=sorted(pool.map(lambda r:freeze(env,r).status_code,rows))
    assert statuses==[200,422]

def test_inactive_calculation_and_encrypted_pdf_block_package(env):
    data,snap,job,_=report_data(env);c=env[0];row=create(env,data)
    c.post(env[2]+f"/{job['id']}/release",json=dict(revision=snap['revision'],reason='원 계산 해제')).raise_for_status()
    assert freeze(env,row).status_code==422
    from epms import monthly_report_pdf,store,sorting as s
    from pypdf import PdfWriter
    from fastapi import HTTPException
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('password');blob=BytesIO();writer.write(blob);raw=blob.getvalue()
    with env[1].state.db.begin() as conn:
        key=conn.execute(s.evidence.insert().values(org=1,name='잠긴증빙.pdf',sha=sha256(raw).hexdigest(),content=raw,data={})).inserted_primary_key[0]
        with pytest.raises(HTTPException,match='암호화'):
            monthly_report_pdf.render(conn,1,dict(documents=[dict(file=key,kind='evidence',name='잠긴증빙.pdf',sha=sha256(raw).hexdigest(),uses=[dict(label='검증',page=1)])]))

def test_changed_source_requires_explicit_acknowledgement(env):
    data,*_=report_data(env);c=env[0];row=create(env,data)
    from epms import sorting as s
    with env[1].state.db.begin() as conn:conn.execute(s.batches.update().where(s.batches.c.id==1).values(revision=2))
    p=c.get(f"/api/org/1/monthly-reports/{row['id']}/preview").json()
    assert any('확정 이후' in w for w in p['warnings']);assert freeze(env,row).status_code==422
    updated=c.put(f"/api/org/1/monthly-reports/{row['id']}",json=dict(data,revision=row['revision'],source_change_ack=True)).raise_for_status().json()
    freeze(env,updated).raise_for_status()
