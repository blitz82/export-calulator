"""Agreement workflows use isolated databases and synthetic PDF fixtures only."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from epms import member_contracts as mc, rules, store
from epms.api import create_app
from test_ocr import searchable_pdf


def number(seed):
    return next(seed+str(i) for i in range(10) if rules.valid_number(seed+str(i)))


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path/'contracts.db')
    client = TestClient(app)
    client.headers['x-epms-token'] = client.get('/api/session').json()['token']
    client.post('/api/organizations?name=계약검증').raise_for_status()
    for i in range(1, 5):
        response = client.post('/api/org/1/data/members', json=[dict(values=dict(
            name=f'회원{i}', number=number(f'12345678{i}'), kind='생산자' if i==1 else '수출업체', approved='2024-01-01'))])
        assert response.raise_for_status().json()[0]['row']['id'] == i
    upload = client.post('/api/org/1/contracts/documents/upload?name=contract.pdf', content=searchable_pdf())
    document = upload.raise_for_status().json()
    yield client, app, document, tmp_path
    client.close(); app.state.db.dispose()


def payload(document, **changes):
    return dict(title='생산·수출 거래약정', kind='공급·수출 계약', producer_id=1, exporter_id=2,
        document_id=document['id'], signed='2026-01-01', start='2026-01-01', end='2026-12-31',
        product='사과', supply='생산자 공급', payment='검수 후 지급', evidence_pages={'parties':1,'payment':1},
        reason='원문 대조 등록', **changes)


def create(client, data):
    return client.post('/api/org/1/contracts', json=data).raise_for_status().json()


def test_document_metadata_preview_duplicate_and_manual_fallback(env, monkeypatch):
    c, app, doc, _ = env
    assert doc['pages']==1 and len(doc['sha'])==64 and doc['created']
    assert doc['extraction']['external_transmission'] is False
    assert c.get(f"/api/org/1/contracts/documents/{doc['id']}/original").content==searchable_pdf()
    assert c.get(f"/api/org/1/contracts/documents/{doc['id']}/page/1").content.startswith(b'\x89PNG')
    assert c.get(f"/api/org/1/contracts/documents/{doc['id']}/page/2").status_code==422
    assert c.post('/api/org/1/contracts/documents/upload?name=again.pdf',content=searchable_pdf()).json()['duplicate']
    # An image-only valid PDF has no text candidates; manual entry still saves.
    blank=searchable_pdf().replace(b'DECL-12345',b'          ')
    uploaded=c.post('/api/org/1/contracts/documents/upload?name=scan.pdf',content=blank).raise_for_status().json()
    assert uploaded['extraction']['pages'][0]['warning'] and not uploaded['extraction']['candidates']
    assert create(c,payload(uploaded))['data']['title']=='생산·수출 거래약정'


def test_multi_party_multiple_contracts_amendments_and_history(env):
    c, app, doc, _=env
    original=create(c,payload(doc))
    second=create(c,payload(doc))
    assert any('겹칩니다' in s for s in second['warnings'])
    assert any('같은 PDF' in s for s in second['warnings'])
    third=payload(doc);third['exporter_id']=3
    create(c,third)
    amendment=payload(doc,parent_id=original['id']);amendment['title']='변경계약'
    changed=create(c,amendment)
    assert changed['parent_id']==original['id']
    assert len(c.get('/api/org/1/contracts?member=1').json())==4
    edit=payload(doc,revision=1);edit['payment']='인도 후 30일 이내 지급'
    result=c.put(f"/api/org/1/contracts/{original['id']}",json=edit).raise_for_status().json()
    assert result['revision']==2
    assert c.put(f"/api/org/1/contracts/{original['id']}",json=edit).status_code==409
    history=c.get(f"/api/org/1/contracts/{original['id']}/history").json()
    assert len(history)==2 and history[1]['snapshot']['data']['payment']=='검수 후 지급'
    assert history[0]['snapshot']['data']['payment']=='인도 후 30일 이내 지급'


def test_self_connection_missing_conflicting_identity():
    p=dict(id=1,number='123',name='같은 이름')
    assert mc.identity(p,p)[0]
    assert mc.identity(p,dict(p,id=2))[0] is False
    assert mc.identity(p,dict(p,id=2))[1]
    valid=number('123456781')
    assert mc.identity(dict(p,number=valid),dict(p,id=2,number=valid))[0]
    assert mc.identity(dict(p,number=valid),dict(p,id=2,number=valid))[1]


def test_self_saved_but_omitted_from_graph_and_role_flexibility(env):
    c, app, doc, _=env
    edit=payload(doc);edit['exporter_id']=1
    assert create(c,edit)['same_business']
    edit=payload(doc);edit.update(producer_id=2,exporter_id=1)
    assert not create(c,edit)['same_business']


@pytest.mark.parametrize('day,changes,state',[
    ('2025-12-31',{},'예정'),('2026-06-01',{},'유효'),('2027-01-01',{},'만료'),
    ('2026-06-01',{'terminated':'2026-05-31'},'해지'),
    ('2027-01-01',{'auto_renew':True},'확인 필요'),('2026-06-01',{'end':''},'확인 필요'),
])
def test_reference_day_status(env,day,changes,state):
    c, app, doc, _=env
    data=payload(doc);data.update(changes);create(c,data)
    row=c.get('/api/org/1/contracts',params={'day':day}).raise_for_status().json()[0]
    assert row['state']==state
    if state=='확인 필요':assert row['status_issues']


def test_member_edit_and_delete_preserve_snapshots_and_revision(env):
    c, app, doc, _=env
    saved=create(c,payload(doc))
    members=c.get('/api/org/1/data/members').json()
    m=members[0];values={k:v for k,v in m.items() if k not in {'id','revision'}};values['name']='새 이름'
    c.post('/api/org/1/data/members',json=[dict(values=values,id=m['id'],revision=m['revision'])]).raise_for_status()
    assert c.get('/api/org/1/contracts').json()[0]['data']['producer']['name']=='회원1'
    c.delete('/api/org/1/data/members/1?revision=2').raise_for_status()
    row=c.get('/api/org/1/contracts').json()[0]
    assert row['producer_id'] is None and row['data']['producer']['id']==1
    assert row['data']['producer']['name']=='회원1' and row['status_issues']
    edit=payload(doc,revision=1);edit['producer_id']=None;edit['memo']='삭제 후 메모 수정'
    assert c.put(f"/api/org/1/contracts/{saved['id']}",json=edit).raise_for_status().json()['revision']==2
    assert c.get(f"/api/org/1/contracts/{saved['id']}/history").json()[1]['snapshot']['producer_id']==1


@pytest.mark.parametrize('changes',[
    {'start':'2026-02-30'}, {'end':'2025-12-31'}, {'quantity':'NaN','unit':'kg'},
    {'amount':'-1','currency':'KRW'}, {'quantity':'10'}, {'evidence_pages':{'payment':2}},
    {'review':'확인 완료','evidence_pages':{}}, {'producer_id':9999},
])
def test_invalid_input(env,changes):
    c, app, doc, _=env;data=payload(doc);data.update(changes)
    assert c.post('/api/org/1/contracts',json=data).status_code in {404,422}
    assert not c.get('/api/org/1/contracts').json()


def test_scoping_immutable_pdf_and_backup_restore(env):
    c, app, doc, tmp=env
    saved=create(c,payload(doc))
    changed_pdf=c.post('/api/org/1/contracts/documents/upload?name=new.pdf',content=searchable_pdf().replace(b'DECL-12345',b'NEW--12345')).raise_for_status().json()
    edit=payload(changed_pdf,revision=1)
    assert c.put(f"/api/org/1/contracts/{saved['id']}",json=edit).status_code==422
    edit=payload(doc,revision=1);edit['exporter_id']=3
    assert c.put(f"/api/org/1/contracts/{saved['id']}",json=edit).status_code==422
    with app.state.db.begin() as conn:
        conn.execute(store.organizations.insert().values(id=2,name='다른 품목'))
    assert c.get(f"/api/org/2/contracts/documents/{doc['id']}").status_code==404
    assert c.get(f"/api/org/2/contracts/{saved['id']}/history").status_code==404
    foreign=payload(doc)
    assert c.post('/api/org/2/contracts',json=foreign).status_code==404
    original=c.get('/api/backup').raise_for_status().content
    restored=tmp/'restored.db';restored.write_bytes(original)
    other=create_app(restored)
    try:
        with TestClient(other) as rc:
            rc.headers['x-epms-token']=rc.get('/api/session').json()['token']
            assert rc.get('/api/org/1/contracts').json()[0]['id']==saved['id']
            assert rc.get(f"/api/org/1/contracts/documents/{doc['id']}/original").content==searchable_pdf()
            assert len(rc.get(f"/api/org/1/contracts/{saved['id']}/history").json())==1
        with other.state.db.connect() as conn:
            assert conn.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
    finally:
        other.state.db.dispose()


def test_upgrade_existing_database_retains_member_data(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import create_engine
    engine=create_engine('sqlite:///'+(tmp_path/'old.db').as_posix())
    config=Config();config.set_main_option('script_location',str(Path(store.__file__).with_name('migrations')))
    with engine.begin() as conn:
        config.attributes['connection']=conn;command.upgrade(config,'0004')
        conn.execute(store.organizations.insert().values(id=1,name='기존 품목'))
    engine.dispose()
    upgraded=store.engine_for(tmp_path/'old.db')
    try:
        with upgraded.connect() as conn:
            assert conn.execute(select(store.organizations.c.name)).scalar_one()=='기존 품목'
            assert conn.execute(mc.contracts.select()).all()==[]
            assert conn.exec_driver_sql('select version_num from alembic_version').scalar_one()=='0010'
    finally:upgraded.dispose()


def test_annual_midyear_single_product_and_multiple_hs(env):
    c, app, doc, _=env
    annual=payload(doc,contract_year=2026,period_type='연간 계약')
    annual['product']='다른 품목을 보내도 통합조직 품목 사용'
    first=create(c,annual)
    assert first['data']['product']=='계약검증'
    assert first['data']['contract_year']==2026
    assert first['data']['start']=='2026-01-01' and first['data']['end']=='2026-12-31'
    bad=dict(annual,start='2026-07-01')
    assert c.post('/api/org/1/contracts',json=bad).status_code==422
    bad=dict(annual,contract_year=2027)
    assert c.post('/api/org/1/contracts',json=bad).status_code==422
    mid=dict(annual,period_type='기간 지정',signed='2026-07-15',start='2026-07-15')
    row=create(c,mid)
    assert row['data']['contract_year']==2026 and row['data']['start']=='2026-07-15'
    listed=c.get('/api/org/1/contracts?day=2026-07-14').json()
    assert next(r for r in listed if r['id']==row['id'])['state']=='예정'
    codes=['0808100000','0808300000']
    c.put('/api/org/1/policies/2026',json=dict(cutoff='2026-12-31',basis='shipped',hs=codes,reason='복수 HS 검증')).raise_for_status()
    assert c.get('/api/org/1/policies').json()[0]['hs']==codes


def test_legacy_contract_year_defaults_without_rewriting_history(env):
    c, app, doc, _=env
    row=create(c,payload(doc))
    with app.state.db.begin() as conn:
        data=dict(row['data']);data.pop('contract_year');data.pop('period_type')
        conn.execute(mc.contracts.update().where(mc.contracts.c.id==row['id']).values(data=data))
    result=c.get('/api/org/1/contracts').json()[0]
    assert result['data']['contract_year']==2026 and result['data']['period_type']=='기간 지정'
    with app.state.db.connect() as conn:
        assert 'contract_year' not in conn.execute(select(mc.contracts.c.data)).scalar_one()
