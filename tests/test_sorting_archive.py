"""Production retirement and archive release tests; old writers exist only in fixture setup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from epms.api import create_app
from epms import sorting as s,store
from test_sorting import setup,payload
from test_sorting_cost_review import expense,pay
from test_sorting_paper import submitted,decide,pay as bank_pay

@contextmanager
def production(app):
    current=create_app(app.state.db.url.database)
    try:
        with TestClient(current) as c:
            c.headers['x-epms-token']=c.get('/api/session').json()['token']
            yield c,current
    finally:current.state.db.dispose()

def test_old_writers_are_not_installed_in_production(setup):
    app,_,base,*_=setup
    with production(app) as (c,current):
        expected={'/api/org/{org}/sorting/facilities','/api/org/{org}/sorting/facilities/{key}/revise','/api/org/{org}/sorting/archive/{key}/release','/api/org/{org}/sorting/{retired:path}'}
        writers={r.path for r in current.routes if getattr(r,'path','').startswith('/api/org/{org}/sorting/') and getattr(r,'methods',set())&{'POST','PUT','PATCH','DELETE'}}
        assert writers==expected
        for path in ['/claims','/paper-cases','/records','/simulate','/policy-drafts','/cost-payment-history','/batches','/claims/1/transition','/paper-cases/1/payments','/evidence']:
            assert c.post(base+path,json={}).status_code==410
        assert c.post(base+'/claims',json={},headers={'x-epms-token':'invalid'}).status_code==403
        assert not c.get(base+'/claims').json()

def test_archive_releases_both_balances_preserves_sources_and_report(setup):
    app,old,base,evidence,facility,batch=setup
    claim=old.post(base+'/claims',json=payload(batch,'600')).raise_for_status().json()['id']
    cost=expense(old,base,facility,evidence);pay(old,base,cost,evidence,'bank-success','SUCCESS')
    old.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='400')).raise_for_status()
    with production(app) as (c,current):
        before=c.get(base+f'/archive/{claim}').raise_for_status().json()
        assert before['active_quantity']=='600' and before['active_cost']=='400' and before['can_release']
        catalog=c.get('/api/org/1/settlements/catalog').json()
        assert catalog['batches'][0]['available']=='400' and catalog['declarations'][0]['available']=='400'
        with current.state.db.connect() as conn:
            original_batch=dict(conn.execute(s.batches.select()).mappings().one());original_line=dict(conn.execute(store.declarations.select()).mappings().one())
        r=c.post(base+f'/archive/{claim}/release',json=dict(revision=before['revision'],reason='종전 예약 종료 후 새 정산으로 전환')).raise_for_status().json()
        assert r['active_quantity']=='0' and r['active_cost']=='0' and not r['can_release']
        assert c.get(base+f'/claims/{claim}/report').content.startswith(b'PK')
        after=c.get('/api/org/1/settlements/catalog').json()
        assert after['batches'][0]['available']=='1000' and after['declarations'][0]['available']=='1000'
        with current.state.db.connect() as conn:
            assert original_batch==dict(conn.execute(s.batches.select()).mappings().one())
            assert original_line==dict(conn.execute(store.declarations.select()).mappings().one())
            assert conn.execute(select(s.expense_allocations.c.active)).scalar_one()==0
            assert conn.execute(select(store.audit.c.entity).where(store.audit.c.entity=='sorting_archive_release')).scalar_one()=='sorting_archive_release'
        assert c.post(base+f'/archive/{claim}/release',json=dict(revision=before['revision'],reason='중복 요청')).status_code==409

def test_archive_cas_blank_reason_scope_and_concurrent_release(setup):
    app,old,base,evidence,facility,batch=setup
    claim=old.post(base+'/claims',json=payload(batch)).json()['id']
    with production(app) as (c,_):
        assert c.get('/api/org/2/sorting/archive/'+str(claim)).status_code==404
        assert c.post(base+f'/archive/{claim}/release',json=dict(revision=1,reason='  ')).status_code==422
        def release(_):return c.post(base+f'/archive/{claim}/release',json=dict(revision=1,reason='동시 해제'))
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(release,[1,2]))
        assert sorted(r.status_code for r in result)==[200,409]

def test_paper_locked_claim_remains_locked_and_readable(setup):
    app,old,base,*_=setup
    claim,row,case,body=submitted(setup)
    with production(app) as (c,_):
        state=c.get(base+f'/archive/{claim}').raise_for_status().json()
        assert state['lock_reason'] and not state['can_release'] and state['has_active_reservations']
        response=c.post(base+f'/archive/{claim}/release',json=dict(revision=state['revision'],reason='삭제로 우회 시도'))
        assert response.status_code==409
        assert state['paper_cases'][0]['report_kind']=='SUBMISSION'
        assert c.get(base+f'/paper-cases/{case}/report?kind=SUBMISSION').content.startswith(b'PK')
        assert c.get(base+f'/paper-cases/{case}/report').content.startswith(b'PK')
        decision=decide(old,base,case,row,setup[3]).raise_for_status().json()['id']
        bank_pay(old,base,case,decision,row,setup[3]).raise_for_status()
        assert c.get(base+f'/archive/{claim}').json()['paper_cases'][0]['report_kind']=='RESULT'
        assert c.get(base+f'/paper-cases/{case}/report?kind=RESULT').content.startswith(b'PK')
        assert c.post(base+f'/paper-cases/{case}/close',json={}).status_code==410
        assert c.get(base+f'/archive/{claim}').json()['active_quantity']==state['active_quantity']

def test_feature_disabled_does_not_enable_archive(setup,monkeypatch):
    app,_,base,*_=setup
    monkeypatch.setenv('EPMS_SORTING_ENABLED','0')
    with production(app) as (c,_):
        assert c.get(base+'/archive').status_code==404
        assert c.post(base+'/archive/1/release',json=dict(revision=1,reason='사유')).status_code==404
