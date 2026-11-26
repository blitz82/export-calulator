from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import sqlite3
from fastapi.testclient import TestClient
from epms.api import create_app
from epms import store,sorting as s,settlement as st,sorting_receipts as intake
from test_settlement import env,source,declaration,payload,draft,confirm,cost

def receipt(env,**changes):
    d=dict(request_key=uuid4().hex,facility_id=env[4],title='7월 선별장 자료',period_type='월별',start='2026-07-01',end='2026-07-31',received='2026-08-02',documents=[dict(file=env[3],page=1,kind='선별일지')])
    d.update(changes);r=env[0].post('/api/org/1/sorting-receipts',json=d).raise_for_status().json();return r,d

def test_receipt_independent_of_job_draft_zero_unknown_and_states(env):
    c=env[0];r,d=receipt(env)
    assert not c.get(env[2]).json()
    assert c.get('/api/org/1/sorting-receipts').json()[0]['state']=='접수'
    b,original=source(env,'',receipt_id=r['id'],producer_id=None,confirmed=False,start='',end='',name='미확인 초안')
    catalog=c.get(env[2]+'/catalog?records_only=true').json();row=next(x for x in catalog['batches'] if x['id']==b)
    assert row['data']['quantity_unknown'] and not row['ready'] and row['data']['export_quantity']==''
    assert c.get('/api/org/1/sorting-receipts').json()[0]['export_quantity'] is None
    original.update(export_quantity='0',start='2026-07-01',end='2026-07-01',producer_id=1,confirmed=True,revision=1,change_reason='원문에서 수출 없음 확인')
    c.put(env[2]+f'/sources/{b}',json=original).raise_for_status()
    case=c.get('/api/org/1/sorting-receipts').json()[0]
    assert case['state']=='연결 준비 완료' and case['export_quantity']=='0'
    assert not c.get(env[2]+'/catalog').json()['batches'][0]['ready']

def test_receipt_idempotency_revision_and_attachments(env):
    c=env[0];r,d=receipt(env)
    assert c.post('/api/org/1/sorting-receipts',json=d).json()['id']==r['id']
    assert c.post('/api/org/1/sorting-receipts',json=dict(d,title='다른 내용')).status_code==409
    b,_=source(env,receipt_id=r['id'])
    assert c.put(f'/api/org/1/sorting-receipts/{r["id"]}',json=dict(d,revision=1,reason='파일 제거',documents=[])).status_code==422
    assert c.put(f'/api/org/1/sorting-receipts/{r["id"]}',json=dict(d,revision=99,reason='동시 변경')).status_code==409
    assert c.put(f'/api/org/1/sorting-receipts/{r["id"]}',json=dict(d,revision=1,reason='메모 보완',memo='완료')).is_success
    assert c.get(f'/api/org/1/sorting-receipts/{r["id"]}/history').json()

def test_legacy_attach_keeps_source_and_cost_ids_and_versions(env):
    c=env[0];b,_=source(env);expense,_=cost(env,b);r,d=receipt(env,documents=[])
    for kind,target,revision in [('source',b,1),('cost',expense['id'],2)]:
        result=c.post(f'/api/org/1/sorting-receipts/{r["id"]}/attach',json=dict(revision=revision,kind=kind,target=target,target_revision=1,reason='종전 자료 묶기')).raise_for_status().json()
    cat=c.get(env[2]+'/catalog').json();assert len(cat['batches'])==1 and len(cat['expenses'])==1
    assert cat['batches'][0]['id']==b and cat['batches'][0]['revision']==2
    assert cat['expenses'][0]['id']==expense['id'] and cat['expenses'][0]['data']['revision']==2
    assert len(c.get('/api/org/1/sorting-receipts').json()[0]['data']['documents'])==1

def test_corrections_preserve_pdf_and_invalidate_old_connections(env):
    c=env[0];r,d=receipt(env);b,original=source(env,receipt_id=r['id']);decl=declaration(env)
    data=payload(env,[(b,decl,'800','800')]);job=draft(env,data);saved=confirm(env,job,data).raise_for_status().json()
    pdf=c.get(env[2]+f'/results/{saved["id"]}/pdf').content
    correction,_=receipt(env,title='정정 접수',kind='정정 제출',parent_id=r['id'],reason='계근 정정',documents=[])
    attached=c.post(f'/api/org/1/sorting-receipts/{correction["id"]}/attach',json=dict(revision=1,kind='source',target=b,target_revision=1,reason='기존 물량 정정')).raise_for_status().json()
    assert attached['affected_jobs'][0]['id']==job['id']
    original.update(receipt_id=correction['id'],revision=2,export_quantity='900',change_reason='계근값 원문 정정')
    c.put(env[2]+f'/sources/{b}',json=original).raise_for_status()
    assert len(c.get(env[2]+'/catalog').json()['batches'])==1
    updated=dict(data,revision=saved['revision'],request_key='new-confirm-request')
    assert c.post(env[2]+f'/{job["id"]}/confirm',json=updated).status_code==422
    assert c.get(env[2]+f'/results/{saved["id"]}/pdf').content==pdf
    cases=c.get('/api/org/1/sorting-receipts').json();assert all(case['source_count']==1 for case in cases)

def test_receipt_hold_cannot_be_bypassed_by_omitting_binding(env):
    c=env[0];r,d=receipt(env,needs_supplement=True,supplement_note='원문 보완 대기');b,original=source(env,receipt_id=r['id'])
    original.update(revision=1,receipt_id=None,change_reason='메모 변경')
    c.put(env[2]+f'/sources/{b}',json=original).raise_for_status()
    decl=declaration(env);data=payload(env,[(b,decl,'10','10')]);data['lines'][0]['batch_revision']=2
    job=draft(env,data);response=confirm(env,job,data)
    assert response.status_code==422 and '보완 필요' in response.text

def test_monthly_parent_total_does_not_add_children_twice(env):
    c=env[0];r,_=receipt(env)
    root,_=source(env,'1000',name='월 합계',receipt_id=r['id'],producer_id=None,period_type='월별',end='2026-07-31')
    source(env,'400',name='분할1',receipt_id=r['id'],parent_id=root)
    source(env,'600',name='분할2',receipt_id=r['id'],parent_id=root)
    result=c.get('/api/org/1/sorting-receipts').json()[0]
    assert result['export_quantity']=='1000' and result['source_count']==3 and result['state']=='연결 준비 완료'

def test_receipt_scope_pages_cycle_and_backup(env):
    c,app,path,doc,f,m,tmp=env;r,d=receipt(env)
    assert c.post('/api/org/1/sorting-receipts',json=dict(d,request_key=uuid4().hex,documents=[dict(file=doc,page=2)])).status_code==422
    second,_=receipt(env,parent_id=r['id'])
    assert c.put(f'/api/org/1/sorting-receipts/{r["id"]}',json=dict(d,revision=1,parent_id=second['id'],reason='순환')).status_code==422
    with app.state.db.begin() as conn:conn.execute(store.organizations.insert().values(id=2,name='다른 품목'))
    assert c.get(f'/api/org/2/sorting-receipts/{r["id"]}/history').status_code==404
    file=tmp/'receipt-backup.db';file.write_bytes(c.get('/api/backup').content)
    copy=create_app(file)
    try:
        with TestClient(copy) as client:
            client.headers['x-epms-token']=client.get('/api/session').json()['token']
            assert len(client.get('/api/org/1/sorting-receipts').json())==2
            assert client.get(path+f'/documents/{doc}/original').content.startswith(b'%PDF')
    finally:copy.state.db.dispose()

def test_draft_cost_unknown_amount_and_field_evidence(env):
    c=env[0];r,_=receipt(env)
    expense=dict(facility_id=env[4],receipt_id=r['id'],title='급여 미확인',external_id='draft-cost',start='',end='',gross='',evidence=[])
    response=c.post(env[2]+'/costs',json=expense).raise_for_status().json();assert '발생액 미확인' in response['issues']
    b,original=source(env,'12',receipt_id=r['id'],field_evidence={'export_quantity':dict(file=env[3],page=1)})
    original.update(revision=1,change_reason='근거 변경',field_evidence={'export_quantity':dict(file=env[3],page=2)})
    assert c.put(env[2]+f'/sources/{b}',json=original).status_code==422
