"""Confirmation is gated by at least one manufacturer/exporter agreement."""
import pytest
from epms import contract_gate, rules
from test_member_contracts import env, payload, create


def declaration(c, **changes):
    members=c.get('/api/org/1/data/members').json()
    values=dict(number='12345-26-123456',line='001',version='1',declared='2026-07-14',shipped='2026-07-15',
        usd='100',kg='10',hs='0808100000',scope='대상',status='유효')
    for role, member in zip(('maker','agent','owner'),members):
        values.update({role:member['number'],role+'_name':member['name'],role+'_state':'회원 대조'})
    values.update(changes)
    return values


def setup_runs(c, values):
    response=c.post('/api/org/1/data/lines',json=[dict(values=values)]).raise_for_status().json()[0]
    ids=[]
    for year in (2025,2026):
        c.put(f'/api/org/1/policies/{year}',json=dict(cutoff=f'{year}-12-31',basis='shipped',hs=['0808100000'],reason='합성 검증')).raise_for_status()
        ids.append(c.post(f'/api/org/1/runs?year={year}').raise_for_status().json()['id'])
    return response,ids


def test_missing_contract_visible_in_all_validation_and_blocks_final(env):
    c,app,doc,_=env
    values=declaration(c)
    saved,ids=setup_runs(c,values)
    assert saved['row'] and any(p['code']=='D090' for p in saved['issues'])
    problems=c.get('/api/org/1/line-checks').json()[str(saved['row']['id'])]
    assert any(p['code']=='D090' and '계약서 등록' in p['message'] for p in problems)
    preview=c.post('/api/org/1/data/lines/validate',json=dict(values=values)).json()
    assert any(p['code']=='D090' for p in preview)
    run=c.get(f'/api/org/1/runs/{ids[1]}').json()
    assert not run['ready'] and run['actual_usd']=='100'
    params=dict(previous_id=ids[0],run_id=ids[1],final=True)
    blocked=c.post('/api/org/1/annual-submission',params=params)
    assert blocked.status_code==400 and '계약서 등록' in blocked.json()['detail']
    blocked=c.post('/api/org/1/submission',params=dict(first_id=ids[0],second_id=ids[1],sheet='임의',final=True))
    assert blocked.status_code==400 and '계약서 등록' in blocked.json()['detail']


@pytest.mark.parametrize('exporter',[2,3])
def test_either_exporter_agreement_suffices_and_ignores_today(env,exporter):
    c,app,doc,_=env
    agreement=payload(doc);agreement['exporter_id']=exporter
    agreement.update(start='2026-07-01',end='2026-07-31')
    create(c,agreement)
    saved,ids=setup_runs(c,declaration(c))
    assert not any(p['code']=='D090' for p in saved['issues'])
    run=c.get(f'/api/org/1/runs/{ids[1]}').json()
    assert run['ready']
    readiness=c.get('/api/org/1/annual-submission',params=dict(previous_id=ids[0],run_id=ids[1])).json()
    assert readiness['blocker'] is None


@pytest.mark.parametrize('changes',[
    {'start':'2026-08-01'},{'end':'2026-07-14'},{'terminated':'2026-07-15'},
    {'end':'2026-07-14','auto_renew':True},{'end':''},{'exporter_id':4},
])
def test_invalid_period_or_wrong_party_blocks(env,changes):
    c,app,doc,_=env
    agreement=payload(doc);agreement.update(changes);create(c,agreement)
    saved,ids=setup_runs(c,declaration(c))
    assert any(p['code']=='D090' for p in saved['issues'])
    assert not c.get(f'/api/org/1/runs/{ids[1]}').json()['ready']


def test_same_business_exempt_and_nonmember_cannot_satisfy(env):
    c,app,doc,_=env
    values=declaration(c)
    values.update(owner=values['maker'],owner_name=values['maker_name'])
    saved,ids=setup_runs(c,values)
    assert not any(p['code']=='D090' for p in saved['issues'])
    create(c,payload(doc))
    values=declaration(c,agent_state='회원사 아님')
    members={rules.business_number(m['number']):m for m in c.get('/api/org/1/data/members').json()}
    with app.state.db.connect() as conn:
        assert contract_gate.evaluate(values,members,contract_gate.context(conn,1))[0]


def test_contract_change_invalidates_review_and_rechecks_old_run(env):
    c,app,doc,_=env
    agreement=create(c,payload(doc))
    saved,ids=setup_runs(c,declaration(c))
    params=dict(previous_id=ids[0],run_id=ids[1],final=True)
    assert c.get('/api/org/1/annual-submission',params=params).json()['blocker'] is None
    update=payload(doc,revision=1);update['end']='2026-07-14'
    c.put(f"/api/org/1/contracts/{agreement['id']}",json=update).raise_for_status()
    response=c.post('/api/org/1/annual-submission',params=params)
    assert response.status_code==400 and '계약서 등록' in response.json()['detail']
    response=c.post('/api/org/1/annual-submission',params=params|{'require_current':True})
    assert response.status_code==409 and '계약서 등록' in response.json()['detail']
    review=c.post('/api/org/1/submission-review?year=2026').raise_for_status().json()
    current=next(r for r in review['results'] if r['year']==2026)
    assert not current['ready'] and any(p['code']=='D090' for p in current['checks'])
    # Correcting the agreement requires a fresh review; stale blocked runs cannot be confirmed.
    update['end']='2026-12-31';update['revision']=2
    c.put(f"/api/org/1/contracts/{agreement['id']}",json=update).raise_for_status()
    response=c.post('/api/org/1/annual-submission',params=params)
    assert response.status_code==400 and '다시 검토' in response.json()['detail']
    refreshed=c.post('/api/org/1/submission-review?year=2026').json()
    assert all(r['ready'] for r in refreshed['results'])


def test_excluded_cancelled_and_missing_date(env):
    c,app,doc,_=env
    members={rules.business_number(m['number']):m for m in c.get('/api/org/1/data/members').json()}
    for changes in ({'scope':'제외'},{'status':'취소'}):
        assert contract_gate.evaluate(declaration(c,**changes),members,[])[0]==[]
    assert contract_gate.evaluate(declaration(c,shipped='',declared=''),members,[])[0][0]['code']=='D092'
    create(c,payload(doc))
    with app.state.db.connect() as conn:
        assert contract_gate.evaluate(declaration(c,shipped=''),members,contract_gate.context(conn,1))[0]==[]
