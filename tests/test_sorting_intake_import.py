import csv
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from epms import sorting as s
from test_sorting import setup

HEADERS=['입고번호','농가','품목','일자','수량','단위','품종','색상','규격']
MAPPING=dict(external_id=0,producer_id=1,product=2,date=3,quantity=4,unit=5,variety=6,color=7,grade=8)


def upload(client,base,rows,encoding='utf-8-sig',headers=HEADERS):
    stream=StringIO(newline='');writer=csv.writer(stream);writer.writerow(headers);writer.writerows(rows)
    response=client.post(base+'/evidence?name=synthetic-intake.csv',content=stream.getvalue().encode(encoding))
    assert response.status_code==200,response.text
    return response.json()['id']


def raw(key='00001',quantity='10.000001',**updates):
    values=dict(key=key,producer='synthetic-farm',product='가상품목',day='2026-03-01',quantity=quantity,unit='kg',variety='가상품종',color='가상색',grade='가상규격')
    values.update(updates)
    return list(values.values())


def config(evidence,facility,**changes):
    return dict(dict(evidence_id=evidence,facility_id=facility,encoding='utf-8-sig',system='ERP-TEST',mapping=MAPPING),**changes)


def prepare(client,base,body,rows=[2],key='csv-request-001'):
    response=client.post(base+'/intake-import-preview',json=body)
    assert response.status_code==200,response.text
    return dict(body,preview_hash=response.json()['preview_hash'],rows=rows,request_key=key)


def test_preview_source_provenance_optional_fields_and_retry(setup):
    app,client,base,evidence,facility,batch=setup
    file=upload(client,base,[raw()])
    columns=client.get(base+'/intake-import-columns',params=dict(evidence_id=file))
    assert columns.status_code==200 and columns.json()['headers']==HEADERS
    body=config(file,facility,system=' ERP-TEST ')
    preview=client.post(base+'/intake-import-preview',json=body).json()
    assert preview['valid_rows']==1 and preview['rows'][0]['original']['external_id']=='00001'
    assert client.get(base+'/production-ledger').json()['rows'][0]['kind']=='BATCH'
    data=prepare(client,base,body)
    first=client.post(base+'/intake-import-commit',json=data)
    assert first.status_code==200,first.text
    result=first.json();assert result['created_count']==1 and result['reused_count']==0
    repeated=client.post(base+'/intake-import-commit',json=data).json()
    assert repeated['id']==result['id'] and repeated['created_count']==0 and repeated['reused_count']==1
    another=client.post(base+'/intake-import-commit',json=dict(data,request_key='csv-request-002')).json()
    assert another['created_count']==0 and another['intake_ids']==result['intake_ids']
    rows=client.get(base+'/records').json();intakes=[r for r in rows if r['kind']=='INTAKE']
    assert len(intakes)==1
    value=intakes[0]['data']
    assert value['external_id']=='ERP-TEST:00001' and value['variety']=='가상품종' and value['color']=='가상색'
    assert value['source']['row']==2 and value['source']['cells'][0]=='00001'
    assert value['source']['config']['version']=='intake-csv-1'
    assert len(client.get(base+'/intake-import-history').json())==2
    assert client.post(base+'/intake-import-commit',json=dict(data,rows=[3])).status_code==409


def test_changed_file_same_id_rolls_back_every_new_row(setup):
    app,client,base,evidence,facility,batch=setup
    original=upload(client,base,[raw('00001')])
    data=prepare(client,base,config(original,facility))
    assert client.post(base+'/intake-import-commit',json=data).status_code==200
    changed=upload(client,base,[raw('00002'),raw('00001','11')])
    data=prepare(client,base,config(changed,facility),[2,3],key='csv-changed-001')
    assert client.post(base+'/intake-import-commit',json=data).status_code==409
    rows=client.get(base+'/records').json()
    assert len([r for r in rows if r['kind']=='INTAKE'])==1
    assert len([r for r in rows if r['kind']=='ERP_INTAKE_IMPORT'])==1


def test_row_issues_duplicates_formula_units_quantity_and_date(setup):
    app,client,base,evidence,facility,batch=setup
    file=upload(client,base,[raw('DUP'),raw('DUP'),raw('BAD',unit='BOX',day='2026-02-30',quantity='=2+2'),raw('GOOD')])
    body=config(file,facility);view=client.post(base+'/intake-import-preview',json=body).json()
    assert view['valid_rows']==1
    assert len(view['rows'][2]['issues'])>=3
    data=dict(body,preview_hash=view['preview_hash'],request_key='invalid-rows-001',rows=[2,5])
    assert client.post(base+'/intake-import-commit',json=data).status_code==422
    assert client.get(base+'/intake-import-history').json()==[]
    data['rows']=[5]
    assert client.post(base+'/intake-import-commit',json=data).json()['created_count']==1


def test_stale_mapping_scope_and_source_tamper(setup):
    app,client,base,evidence,facility,batch=setup
    file=upload(client,base,[raw()]);body=config(file,facility)
    data=prepare(client,base,body)
    assert client.post(base+'/intake-import-commit',json=dict(data,system='OTHER')).status_code==409
    assert client.post('/api/org/2/sorting/intake-import-preview',json=body).status_code==404
    assert client.get('/api/org/2/sorting/intake-import-history').json()==[]
    assert client.post(base+'/intake-import-preview',json=dict(body,mapping=dict(MAPPING,unit=4))).status_code==422
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.update().where(s.evidence.c.id==file).values(content=b'changed'))
    assert client.post(base+'/intake-import-commit',json=data).status_code==409


def test_cp949_and_file_limits_headers(setup):
    app,client,base,evidence,facility,batch=setup
    file=upload(client,base,[raw()],encoding='cp949')
    assert client.get(base+'/intake-import-columns',params=dict(evidence_id=file,encoding='utf-8-sig')).status_code==422
    assert client.post(base+'/intake-import-preview',json=config(file,facility,encoding='cp949')).json()['valid_rows']==1
    duplicate=upload(client,base,[raw()],headers=['중복']*9)
    assert client.get(base+'/intake-import-columns',params=dict(evidence_id=duplicate)).status_code==422
    excessive=upload(client,base,[raw(str(i)) for i in range(5001)])
    assert client.get(base+'/intake-import-columns',params=dict(evidence_id=excessive)).status_code==422


def test_concurrent_same_request_creates_stock_once(setup):
    app,client,base,evidence,facility,batch=setup
    file=upload(client,base,[raw()]);data=prepare(client,base,config(file,facility))
    def post(_):
        response=client.post(base+'/intake-import-commit',json=data)
        assert response.status_code==200,response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(post,range(2)))
    assert results[0]['id']==results[1]['id']
    assert sum(r['created_count'] for r in results)==1
    assert len(client.get(base+'/intake-import-history').json())==1
