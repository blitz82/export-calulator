"""Synthetic acceptance data only. Every database is explicitly temporary."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from epms import sorting as s, sorting_books, store
from legacy_sorting_fixture import create_legacy_app as create_app
from epms import sorting_finance as finance
from epms import sorting_records, sorting_documents


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('EPMS_SORTING_ENABLED', '1')
    app = create_app(tmp_path / 'test.db')
    with app.state.db.begin() as conn:
        conn.execute(store.organizations.insert().values(id=1, name='가상품목'))
        conn.execute(store.organizations.insert().values(id=2, name='별도품목'))
        conn.execute(store.companies.insert().values(number='1234567834'))
        conn.execute(store.memberships.insert().values(org=1, number='1234567834', revision=1, data=dict(name='가상조합')))
        conn.execute(store.declarations.insert().values(id=1, org=1, revision=1, deleted=0,
            data=dict(number='SYNTHETIC-26-000001X', line='001', version='1', kg='1000', status='유효', scope='대상')))
    client = TestClient(app)
    client.headers['x-epms-token'] = app.state.token
    base = '/api/org/1/sorting'
    evidence = client.post(base+'/evidence?name=synthetic.txt', content=b'synthetic evidence').json()['id']
    facility = client.post(base+'/facilities', json=dict(name='가상선별장', product='가상품목', member_number='1234567834',
        designation='TEST-1', valid_from='2026-01-01', valid_to='2027-12-31', evidence_id=evidence)).json()['id']
    batch = client.post(base+'/batches', json=dict(facility_id=facility, external_id='TEST-BATCH-1', work_date='2026-03-01',
        product='가상품목', quantity='1000', unit='kg', evidence_id=evidence)).json()['id']
    yield app, client, base, evidence, facility, batch
    client.close()
    app.state.db.dispose()


def payload(batch, quantity='600', key='request-0001'):
    return dict(request_key=key, year=2027, product='가상품목', profile='INTEGRATED_2027_DRAFT',
        lines=[dict(declaration_id=1, declaration_revision=1, batch_id=batch, batch_revision=1, quantity=quantity)])


@pytest.mark.parametrize('q,u,expected', [('288518','211','60877298'), ('267282','235','62811270'),
    ('339044.6','235','79675481.0'), ('35562','718','25533516')])
def test_T01_T02_T04_reference(q,u,expected):
    result = s.reference_amount(q,u)
    assert result['quantity_limit'] == expected
    assert result['reference_review_amount'] is None and result['payable_amount'] is None


@pytest.mark.parametrize('cost,expected', [(None,None), ('0','0'), ('8000000','8000000'), ('12000000','10000000')])
def test_T09_T10_T19_null_zero(cost,expected):
    assert s.reference_amount('10000','1000',cost)['reference_review_amount'] == expected


@pytest.mark.parametrize('value', ['', '-1', 'NaN', 'Infinity', '0.0000001', '1e3', '1,000', 10, '99999999999999'])
def test_numeric_rejection(value):
    with pytest.raises(s.SortingError):
        s.kg(value)


def test_large_fractional_product_does_not_round_intermediate():
    from decimal import localcontext
    value='1000000000000.123456'
    with localcontext() as context:
        context.prec=80
        expected=Decimal(value)*Decimal(value)
    assert Decimal(s.reference_amount(value,value)['quantity_limit'])==expected


def test_T06_reservation_idempotency_and_release(setup):
    app, client, base, evidence, facility, batch = setup
    first = client.post(base+'/claims',json=payload(batch))
    assert first.status_code == 200
    assert client.post(base+'/claims',json=payload(batch)).json() == first.json()
    assert client.post(base+'/claims',json=payload(batch,'400','request-0002')).status_code == 200
    assert client.post(base+'/claims',json=payload(batch,'0.000001','request-0003')).status_code == 409
    assert client.post(base+'/claims',json=payload(batch,'500')).status_code == 409
    claim = first.json()['id']
    assert client.post(f'{base}/claims/{claim}/transition',json=dict(revision=1, action='WITHDRAW',reason='합성 시험')).status_code == 200
    assert client.post(base+'/claims',json=payload(batch,'600','request-0004')).status_code == 200
    with app.state.db.connect() as conn:
        assert sum(conn.execute(s.allocations.select().with_only_columns(s.allocations.c.quantity).where(s.allocations.c.active==1)).scalars()) == s.kg('1000')


def test_T06_concurrent_overflow(setup):
    app, client, base, evidence, facility, batch = setup
    def submit(i):
        return client.post(base+'/claims',json=payload(batch,'600',f'concurrent-{i}')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2))) == [200,409]


def test_T08_T14_T18_guards_scope_snapshot(setup):
    app, client, base, evidence, facility, batch = setup
    entity_id = client.post(base+'/claims',json=payload(batch)).json()['id']
    for action in ('approve','pay','admin','batch','retry'):
        assert client.post(f'{base}/claims/{entity_id}/{action}',json=dict(role='admin', approved_amount='1')).status_code == 403
    assert client.post(f'{base}/claims/{entity_id}/transition',json=dict(action='APPROVE',revision=1,reason='TEST')).status_code == 403
    assert client.get(f'{base}/claims/{entity_id}/report?official=true').status_code == 403
    assert client.get(f'/api/org/2/sorting/claims/{entity_id}').status_code == 404
    assert client.get(f'/api/org/2/sorting/evidence/{evidence}').status_code == 404
    report = client.get(f'{base}/claims/{entity_id}').json()
    assert report['approved_amount'] is None
    assert {'SORT_E07','SORT_E08','SORT_W07'} <= {i['code'] for i in report['issues']}
    original_hash = report['lines'][0]['source']['hash']
    with app.state.db.begin() as conn:
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,
            data=dict(number='SYNTHETIC-26-000001X',line='001',version='2',kg='500',status='유효',scope='대상')))
    changed = client.get(f'{base}/claims/{entity_id}').json()
    assert changed['lines'][0]['source']['hash'] == original_hash
    assert any(i['field']=='source' for i in changed['issues'])
    assert client.post(base+'/claims',json=payload(batch,'1','request-new1')).status_code == 409
    book = load_workbook(BytesIO(client.get(f'{base}/claims/{entity_id}/report').content))
    assert '검토용' in book.active['A1'].value
    book.close()
    assert "filename*=UTF-8''sorting-review.xlsx" in client.get(f'{base}/claims/{entity_id}/report').headers['content-disposition']


def test_evidence_preview_no_write_and_template(setup):
    app, client, base, evidence, facility, batch = setup
    again = client.post(base+'/evidence?name=renamed.txt',content=b'synthetic evidence').json()
    assert again['id']==evidence and again['reused']
    template = sorting_books.template()
    assert sorting_books.preview(template)['requires_sheet_selection']
    result = sorting_books.preview(template,'선별 검토')
    assert len(result['rows'])==1 and result['rows'][0]['cells']['number']=='A2'
    assert result['rows'][0]['original']['quantity']=='100.000001'


def test_secondary_header_and_spaced_total_are_not_import_rows():
    from openpyxl import Workbook
    book=Workbook()
    sheet=book.active
    sheet.append(['신고번호','순중량(kg)','공동선별장'])
    sheet.append([None,None,'상세주소'])
    sheet.append(['SYNTHETIC-26-000001X','100','가상시설'])
    sheet.append(['합  계','100',None])
    content=sorting_books.workbook_bytes(book)
    result=sorting_books.preview(content,sheet.title)
    assert len(result['rows'])==1
    assert result['rows'][0]['row']==3


def test_M1_capacity_multiline_and_stale_version(setup):
    app,client,base,evidence,facility,batch=setup
    query=f'{base}/capacity?declaration_id=1&declaration_revision=1&batch_id={batch}'
    assert client.get(query).json()['available']=='1000'
    data=payload(batch,'600')
    data['lines'].append(dict(data['lines'][0],quantity='400'))
    entity_id=client.post(base+'/claims',json=data).json()['id']
    assert client.get(query).json()['available']=='0'
    rows=client.get(f'{base}/claims/{entity_id}').json()['lines']
    assert client.post(f'{base}/claims/{entity_id}/lines/{rows[0]["id"]}',json=dict(revision=1,action='SUPPLEMENT',reason='증빙 보완')).status_code==200
    assert client.get(query).json()['available']=='0'
    assert client.post(f'{base}/claims/{entity_id}/lines/{rows[1]["id"]}',json=dict(revision=1,action='REJECT',reason='오래된 화면')).status_code==409
    assert client.post(f'{base}/claims/{entity_id}/lines/{rows[1]["id"]}',json=dict(revision=2,action='REJECT',reason='부분 반려')).status_code==200
    assert client.get(query).json()['available']=='400'
    report=client.get(f'{base}/claims/{entity_id}').json()
    assert report['quantity']=='600' and report['original_quantity']=='1000'
    assert report['lines'][0]['state']=='SUPPLEMENT' and report['lines'][0]['active']
    book=load_workbook(BytesIO(client.get(f'{base}/claims/{entity_id}/report').content))
    assert book.active['F4'].value=='SUPPLEMENT' and book.active['G4'].value=='유지'
    assert book.active['F5'].value=='REJECT' and book.active['G5'].value=='해제'
    assert '날짜 원본 대조' in book.sheetnames
    book.close()


@pytest.mark.parametrize('work_date,warning',[('2026-01-01',False),('2027-12-31',False),('2025-12-31',True),('2028-01-01',True)])
def test_M1_designation_period_boundaries(setup,work_date,warning):
    app,client,base,evidence,facility,batch=setup
    with app.state.db.begin() as conn:
        conn.execute(s.batches.update().where(s.batches.c.id==batch).values(data=dict(facility_id=facility,
            product='가상품목',work_date=work_date,evidence_id=evidence)))
    entity_id=client.post(base+'/claims',json=payload(batch)).json()['id']
    line=client.get(f'{base}/claims/{entity_id}').json()['lines'][0]
    assert any(p['code']=='SORT_E01' for p in line['issues'])==warning


def test_M1_candidates_versions_and_correction_delta(setup):
    app,client,base,evidence,facility,batch=setup
    found=client.get(base+'/candidates?number=synthetic26-000001').json()
    assert len(found)==1 and found[0]['matching']=='CANDIDATE_ONLY'
    entity_id=client.post(base+'/claims',json=payload(batch)).json()['id']
    with app.state.db.begin() as conn:
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,
            data=dict(number='SYNTHETIC-26-000001X',line='001',version='2',kg='500',status='유효',scope='대상')))
    stale=client.get(f'{base}/capacity?declaration_id=1&declaration_revision=1&batch_id={batch}')
    assert stale.status_code==409
    current=client.get(f'{base}/capacity?declaration_id=1&declaration_revision=2&batch_id={batch}').json()
    assert current['declaration_overallocated']=='100' and current['available']=='0'
    line=client.get(f'{base}/claims/{entity_id}').json()['lines'][0]
    assert line['source']['original']['kg']=='1000' and line['current_kg']=='500' and line['quantity_delta']=='-500'
    assert any(p['field']=='source' for p in line['issues'])
    with app.state.db.begin() as conn:
        conn.execute(store.declarations.insert().values(org=1,revision=1,deleted=0,data=dict(number='SYNTHETIC26-000001X',line='001',version='1',kg='1000',status='유효',scope='대상')))
    assert len(client.get(base+'/candidates?number=SYNTHETIC26000001').json())==2
    assert client.get(f'{base}/capacity?declaration_id=1&declaration_revision=2&batch_id={batch}').status_code==409


def test_M1_dates_are_separate_and_period_warnings_preserve_original(setup):
    app,client,base,evidence,facility,batch=setup
    with app.state.db.begin() as conn:
        conn.execute(s.batches.update().where(s.batches.c.id==batch).values(data=dict(facility_id=facility,
            product='가상품목',work_date='2025-12-31',dispatch_date='2025-12-30',evidence_id=evidence)))
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(data=dict(
            number='SYNTHETIC-26-000001X',line='001',version='1',kg='1000',status='유효',scope='대상',declared='2026-01-02',shipped='2026-02-01')))
    entity_id=client.post(base+'/claims',json=payload(batch)).json()['id']
    review=client.get(f'{base}/claims/{entity_id}').json()
    line=review['lines'][0]
    assert line['dates']==dict(work_date='2025-12-31',dispatch_date='2025-12-30',declared='2026-01-02',planned_shipped='2026-02-01',actual_shipped=None)
    assert {'SORT_E01','SORT_W08','SORT_W02'}<={p['code'] for p in line['issues']}
    assert review['approved_amount'] is None
    assert client.get(f'/api/org/2/sorting/capacity?declaration_id=1&declaration_revision=1&batch_id={batch}').status_code==404


def test_off_existing_contract(tmp_path,monkeypatch):
    monkeypatch.delenv('EPMS_SORTING_ENABLED',raising=False)
    app=create_app(tmp_path/'off.db')
    with TestClient(app) as client:
        client.headers['x-epms-token']=app.state.token
        assert not client.get('/api/sorting-capabilities').json()['feature_enabled']
        assert client.get('/api/org/1/sorting/status').status_code==404
        assert client.get('/api/organizations').status_code==200
    app.state.db.dispose()


def test_migration_backup_restore(tmp_path):
    # Old schema populated without opening any user DB; migrate a consistent backup copy.
    original=tmp_path/'old.db'
    with sqlite3.connect(original) as conn:
        conn.executescript("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE); INSERT INTO organizations VALUES(1,'synthetic'); CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY); INSERT INTO alembic_version VALUES('0003');")
    copied=tmp_path/'copy.db'
    with sqlite3.connect(original) as source, sqlite3.connect(copied) as target:
        source.backup(target)
    engine=store.engine_for(copied)
    engine.dispose()
    with sqlite3.connect(copied) as conn:
        assert conn.execute('SELECT * FROM organizations').fetchall()==[(1,'synthetic')]
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()==('0010',)
    with sqlite3.connect(original) as conn:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()==('0003',)


def test_T03_failed_retry_and_T11_T13_expense_pool(setup):
    app,client,base,evidence,facility,batch=setup
    data=dict(facility_id=facility,external_id='synthetic-expense',month='2026-03',category='EXPORT_SORTING_LABOR',
              amount='1000',separated=True,evidence_id=evidence,separation_evidence_id=evidence,pseudonym='worker-test')
    expense=client.post(base+'/expenses',json=data).json()['id']
    assert client.post(base+'/expenses',json=data).json()['id']==expense
    payment=dict(expense_id=expense,transaction_id='test-fail',amount='1000',status='FAILED',date='2026-03-31',evidence_id=evidence)
    failed=client.post(base+'/cost-payment-history',json=payment).json()['id']
    assert client.get(base+'/expenses').json()[0]['reference_paid_cost']=='0'
    retry=dict(payment,transaction_id='test-retry',status='SUCCESS',retry_of=failed)
    assert client.post(base+'/cost-payment-history',json=retry).status_code==200
    assert client.post(base+'/cost-payment-history',json=dict(retry,transaction_id='duplicate-retry')).status_code==409
    assert client.get(base+'/expenses').json()[0]['reference_paid_cost']=='1000'
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    other=client.post(base+'/claims',json=payload(batch,'400','expense-claim2')).json()['id']
    assert client.post(f'{base}/claims/{claim}/cost-allocation',json=dict(expense_id=expense,amount='800')).status_code==200
    assert client.post(f'{base}/claims/{other}/cost-allocation',json=dict(expense_id=expense,amount='201')).status_code==409
    assert client.post(f'{base}/claims/{other}/cost-allocation',json=dict(expense_id=expense,amount='200')).status_code==200
    for category in ['INTERNAL_LABOR','DOMESTIC_SORTING','PACKAGING','TRANSPORT']:
        assert finance.eligibility(dict(data,category=category))[0]=='EXCLUDED'
    assert finance.eligibility(dict(data,separated=False))[0]=='EXCLUDED'
    assert finance.eligibility(dict(data,category='INSURANCE'))[0]=='PENDING'


def test_T20_union_and_blank():
    portion=dict(portion_id='synthetic-portion-1',quantity='100')
    assert finance.form_amount('1000','718',dict(B=[portion],C=[portion],D=[],E=[]))['F']=='900'
    assert finance.form_amount('1000','718',dict(B=[],C=None,D=[],E=[]))['F'] is None


def test_synthetic_strategy_rate_semantics():
    assert finance.calculate_strategy('TEST','FIXED_FINAL_RATE','100','718')=='71800'
    assert Decimal(finance.calculate_strategy('TEST','COST_BASE_CAP','100','718','60000','0.8'))==Decimal('48000')
    assert Decimal(finance.calculate_strategy('TEST','COST_BASE_CAP','100','718','100000','0.8'))==Decimal('57440')
    assert Decimal(finance.calculate_strategy('TEST','SUBSIDY_CAP','100','718','100000','0.8'))==Decimal('71800')
    with pytest.raises(s.SortingError):
        finance.calculate_strategy('PRODUCTION','FIXED_FINAL_RATE','100','718')
    assert finance.payable_test('TEST','100',[dict(claim_id=1,amount='40')]*3,'100')['payable']=='60'
    assert finance.payable_test('TEST','100',[],'0')['payable']=='0'
    assert finance.payable_test('TEST','100',[],'100','120')['adjustment_candidate']=='-20'


def test_import_commit_atomic_idempotent(setup):
    app,client,base,evidence,facility,batch=setup
    book=load_workbook(BytesIO(sorting_books.template()))
    sheet=book.active
    sheet['F2']='가상품목'
    from epms.sorting_books import workbook_bytes
    content=workbook_bytes(book)
    file_id=client.post(base+'/evidence?name=synthetic.xlsx',content=content).json()['id']
    data=dict(evidence_id=file_id,sheet='선별 검토',facility_id=facility,product='가상품목',rows=[2])
    first=client.post(base+'/import-commit',json=data)
    assert first.status_code==200,first.text
    assert client.post(base+'/import-commit',json=data).json()==first.json()
    count=len(client.get(base+'/batches').json())
    assert client.post(base+'/import-commit',json=dict(data,rows=[2,999])).status_code==422
    assert len(client.get(base+'/batches').json())==count


def test_T16_T17_sanction_scope():
    record=dict(state='DELIBERATION',start='2026-01-01',end='2026-12-31',target='synthetic',product='test',program='TEST',scope='TEST')
    query=dict(day='2026-06-01',target='synthetic',product='test',program='TEST',scope='TEST',approved_test_policy=True)
    assert not sorting_records.sanction_candidate(record,**query)
    assert sorting_records.sanction_candidate(dict(record,state='FINAL'),**query)
    assert not sorting_records.sanction_candidate(dict(record,state='FINAL'),**dict(query,product='other'))
    assert not sorting_records.sanction_candidate(dict(record,state='FINAL'),**dict(query,day='2027-01-01'))


def test_production_shared_ledger(setup):
    app,client,base,evidence,facility,batch=setup
    intake=client.post(base+'/intakes',json=dict(facility_id=facility,external_id='synthetic-intake',product='가상품목',producer_id='producer-test',date='2026-03-01',quantity='1000',unit='kg',evidence_id=evidence)).json()['id']
    transform=dict(facility_id=facility,external_id='production-test',product='가상품목',date='2026-03-01',evidence_id=evidence,
        inputs=[dict(kind='INTAKE',id=intake,quantity='1000')],export_quantity='800',domestic_quantity='100',reject_quantity='50',loss_quantity='50',inventory_quantity='0')
    result=client.post(base+'/transforms',json=transform)
    assert result.status_code==200,result.text
    produced=result.json()['batch_id']
    assert client.post(base+'/transforms',json=dict(transform,external_id='duplicate-test')).status_code==409
    dispatch=client.post(base+'/dispatches',json=dict(batch_id=produced,external_id='dispatch-test',exporter_id='exporter-test',date='2026-03-02',quantity='800',evidence_id=evidence)).json()['id']
    data=payload(produced,'800','production-reservation')
    data['lines'][0]['dispatch_id']=dispatch
    assert client.post(base+'/claims',json=data).status_code==200
    assert client.post(base+'/claims',json=payload(produced,'1','basic-duplicate1')).status_code==409
    another=payload(produced,'0.000001','dispatch-overflow')
    another['lines'][0]['dispatch_id']=dispatch
    assert client.post(base+'/claims',json=another).status_code==409


def test_compound_grouping_no_net_gross_or_reissue_sum():
    row=dict(number='SYNTHETIC-26-000001X',line='001',kg='1000')
    pages=[dict(page=3,text='AIR WAYBILL gross 1500',rows=[dict(row,kg='1500')]),
           dict(page=2,text='수출신고필증 을지',rows=[row]),dict(page=1,text='수출신고필증 갑지',rows=[row])]
    result=sorting_documents.group_pages(pages)
    assert len(result['groups'])==1
    assert result['groups'][0]['net_weight']=='1000'
    assert result['groups'][0]['pages']==[1,2]
    assert result['groups'][0]['transport_candidates']==[3]
    assert result['total_net_weight'] is None and not result['automatic_approval']


def test_row_reject_only_releases_selected_portion(setup):
    app,client,base,evidence,facility,batch=setup
    data=payload(batch,'400')
    data['lines'].append(dict(data['lines'][0],quantity='600'))
    entity_id=client.post(base+'/claims',json=data).json()['id']
    lines=client.get(f'{base}/claims/{entity_id}').json()['lines']
    response=client.post(f'{base}/claims/{entity_id}/lines/{lines[0]["id"]}',json=dict(revision=1,action='REJECT',reason='부분 반려 시험'))
    assert response.status_code==200
    assert client.get(f'{base}/claims/{entity_id}').json()['quantity']=='600'
    assert client.get(f'{base}/claims/{entity_id}').json()['original_quantity']=='1000'
    assert client.post(base+'/claims',json=payload(batch,'400','partial-release1')).status_code==200
    assert client.post(base+'/claims',json=payload(batch,'0.000001','partial-excess1')).status_code==409


def test_versioned_contract_settlement_and_certificate(setup):
    app,client,base,evidence,facility,batch=setup
    contract=dict(facility_id=facility,product='가상품목',quantity='1000',start='2026-01-01',end='2026-12-31',
                  parties=['producer-test','exporter-test'],terms='합성 계약 및 지급기한',evidence_id=evidence)
    first=client.post(base+'/records',json=dict(kind='CONTRACT',data=contract)).json()['id']
    revised=client.post(base+'/records',json=dict(kind='CONTRACT',data=dict(contract,quantity='900'),previous_id=first))
    assert revised.status_code==200
    assert client.post(base+'/records',json=dict(kind='CONTRACT',data=contract,previous_id=first)).status_code==409
    settlement=dict(contract_id=first,producer_id='producer-test',sales='1000',sorting_deduction='100',other_deduction='50',fee='10',
        adjustment_add='5',adjustment_subtract='0',paid='845',due_date='2026-03-31',state='COMPLETED',evidence_id=evidence)
    assert client.post(base+'/records',json=dict(kind='PRODUCER_SETTLEMENT',data=settlement)).status_code==200
    assert client.post(base+'/records',json=dict(kind='PRODUCER_SETTLEMENT',data=dict(settlement,paid='846'))).status_code==409
    certificate=dict(facility_id=facility,product='가상품목',quantity='1000',member_number='1234567834',issuer='합성 사무국',
        start='2026-01-01',end='2026-12-31',duplicate_statement='조회 미완료',evidence_id=evidence)
    assert client.post(base+'/records',json=dict(kind='CERTIFICATE',data=certificate)).status_code==200


def test_full_old_schema_semantic_backup_migration(tmp_path):
    from sqlalchemy import create_engine
    old=tmp_path/'v135.db'
    engine=create_engine('sqlite:///'+str(old))
    old_tables=[t for t in store.metadata.sorted_tables if not t.name.startswith('sorting_')]
    store.metadata.create_all(engine,tables=old_tables)
    with engine.begin() as conn:
        conn.execute(store.organizations.insert().values(id=1,name='synthetic'))
        conn.execute(store.import_files.insert().values(org=1,sha='synthetic-hash',kind='test',name='synthetic.bin',data=b'synthetic original'))
        conn.exec_driver_sql('CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES('0003')")
    engine.dispose()
    def snapshot(path):
        with sqlite3.connect(path) as conn:
            return {t.name:conn.execute('SELECT * FROM "'+t.name+'" ORDER BY rowid').fetchall() for t in old_tables}
    before=snapshot(old)
    copied=tmp_path/'migration.db'
    with sqlite3.connect(old) as src,sqlite3.connect(copied) as dst:
        src.backup(dst)
    migrated=store.engine_for(copied)
    with migrated.connect() as conn:
        assert str(tmp_path) in conn.exec_driver_sql('PRAGMA database_list').fetchone()[2]
    migrated.dispose()
    assert snapshot(copied)==before
    restored=tmp_path/'restored.db'
    with sqlite3.connect(old) as src,sqlite3.connect(restored) as dst:
        src.backup(dst)
    assert snapshot(restored)==before


def test_150_page_compound_cache_recovery(monkeypatch):
    from reportlab.pdfgen.canvas import Canvas
    output=BytesIO()
    canvas=Canvas(output)
    for i in range(150):
        canvas.drawString(50,700,f'AIR WAYBILL SYNTHETIC {i}')
        canvas.showPage()
    canvas.save()
    saved={}
    result=sorting_documents.extract(output.getvalue(),saved.get,lambda start,pages:saved.update({start:pages}))
    assert len(result['documents'])==150
    assert sorted(saved)==[0,30,60,90,120]
    assert result['documents'][-1]['page']==150
    assert result['groups']==[]
    monkeypatch.setattr(sorting_documents.ocr,'extract',lambda _:pytest.fail('completed chunks must be reused'))
    replay=sorting_documents.extract(output.getvalue(),saved.get)
    assert replay==result
