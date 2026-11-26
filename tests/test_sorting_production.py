from concurrent.futures import ThreadPoolExecutor
from epms import sorting as s
from test_sorting import setup,payload


def intake(client,base,facility,evidence,key,quantity='100'):
    response=client.post(base+'/intakes',json=dict(facility_id=facility,external_id=key,product='가상품목',producer_id='synthetic-producer',date='2026-03-01',quantity=quantity,unit='kg',evidence_id=evidence))
    assert response.status_code==200,response.text
    return response.json()['id']


def transform(facility,evidence,parents,**updates):
    return dict(dict(facility_id=facility,evidence_id=evidence,external_id='synthetic-mixed',product='가상품목',date='2026-03-02',inputs=parents,
        export_quantity='100',domestic_quantity='0',reject_quantity='0',loss_quantity='0',inventory_quantity='0'),**updates)


def parent(kind,entity,quantity):
    return dict(kind=kind,id=entity,quantity=quantity)


def test_multi_input_merge_split_exact_replay_and_ledger(setup):
    app,client,base,evidence,facility,batch=setup
    first=intake(client,base,facility,evidence,'intake-1','60.000001')
    second=intake(client,base,facility,evidence,'intake-2','40')
    body=transform(facility,evidence,[parent('INTAKE',first,'60.000001'),parent('INTAKE',second,'40')],
        export_quantity='80.000001',domestic_quantity='10',reject_quantity='5',loss_quantity='2',inventory_quantity='3')
    result=client.post(base+'/transforms',json=body)
    assert result.status_code==200,result.text
    assert client.post(base+'/transforms',json=body).json()==result.json()
    changed=dict(body,date='2026-03-03')
    assert client.post(base+'/transforms',json=changed).status_code==409
    info=client.get(base+'/production-ledger').json()
    rows={(r['kind'],r['id']):r for r in info['rows']}
    assert rows['INTAKE',first]['available']=='0'
    assert rows['INTAKE',first]['transformed']=='60.000001'
    assert rows['BATCH',result.json()['batch_id']]['quantity']=='80.000001'
    assert all(not r['physical_trace_verified'] for r in info['rows'])
    assert info['inventory_reuse']=='POLICY_PENDING'
    assert len([r for r in client.get(base+'/records').json() if r['kind']=='LOT_TRANSFORM'])==1


def test_no_export_replay_duplicate_parent_overflow_and_atomic_failure(setup):
    app,client,base,evidence,facility,batch=setup
    entity=intake(client,base,facility,evidence,'intake-1')
    bad=transform(facility,evidence,[parent('INTAKE',entity,'60'),parent('INTAKE',entity,'41')],export_quantity='101')
    assert client.post(base+'/transforms',json=bad).status_code==409
    bad=transform(facility,evidence,[parent('INTAKE',entity,'100')],export_quantity='99')
    assert client.post(base+'/transforms',json=bad).status_code==409
    body=transform(facility,evidence,[parent('INTAKE',entity,'100')],export_quantity='0',domestic_quantity='100')
    result=client.post(base+'/transforms',json=body)
    assert result.status_code==200 and result.json()['batch_id'] is None
    assert client.post(base+'/transforms',json=body).json()==result.json()
    assert client.post(base+'/transforms',json=dict(body,external_id='second')).status_code==409


def test_direct_reservation_dispatch_and_reservation_release_count_once(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch,'100')).json()['id']
    outgoing=dict(batch_id=batch,external_id='ship-1',exporter_id='synthetic-exporter',date='2026-03-02',quantity='400',evidence_id=evidence)
    dispatched=client.post(base+'/dispatches',json=outgoing)
    assert dispatched.status_code==200
    dispatch_id=dispatched.json()['id']
    assert client.post(base+'/dispatches',json=outgoing).json()['id']==dispatch_id
    assert client.post(base+'/dispatches',json=dict(outgoing,quantity='401')).status_code==409
    second=payload(batch,'400','dispatch-reservation')
    second['lines'][0]['dispatch_id']=dispatch_id
    assert client.post(base+'/claims',json=second).status_code==200
    body=transform(facility,evidence,[parent('BATCH',batch,'200')],export_quantity='200')
    assert client.post(base+'/transforms',json=body).status_code==200
    row=next(r for r in client.get(base+'/production-ledger').json()['rows'] if r['kind']=='BATCH' and r['id']==batch)
    assert (row['reserved'],row['dispatched'],row['transformed'],row['available'])==('100','400','200','300')
    revision=client.get(base+f'/claims/{claim}').json()['revision']
    assert client.post(base+f'/claims/{claim}/transition',json=dict(revision=revision,action='WITHDRAW',reason='합성 철회')).status_code==200
    row=next(r for r in client.get(base+'/production-ledger').json()['rows'] if r['kind']=='BATCH' and r['id']==batch)
    assert row['available']=='400' and row['reserved']=='0'


def test_dates_scope_product_and_foreign_parent_rollback(setup):
    app,client,base,evidence,facility,batch=setup
    entity=intake(client,base,facility,evidence,'intake-1')
    body=transform(facility,evidence,[parent('INTAKE',entity,'100')],date='2026-02-28')
    assert client.post(base+'/transforms',json=body).status_code==422
    body=transform(facility,evidence,[parent('BATCH',batch,'100')],date='2026-02-28')
    assert client.post(base+'/transforms',json=body).status_code==422
    body=transform(facility,evidence,[parent('INTAKE',entity,'100')],product='다른품목',export_quantity='0',domestic_quantity='100')
    assert client.post(base+'/transforms',json=body).status_code==422
    assert client.get('/api/org/2/sorting/production-ledger').json()['rows']==[]
    assert client.post('/api/org/2/sorting/transforms',json=transform(facility,evidence,[parent('INTAKE',entity,'100')])).status_code==404
    with app.state.db.begin() as conn:
        conn.execute(s.records.update().where(s.records.c.id==entity).values(org=2))
    assert client.post(base+'/transforms',json=transform(facility,evidence,[parent('BATCH',batch,'10'),parent('INTAKE',entity,'90')])).status_code==404
    row=next(r for r in client.get(base+'/production-ledger').json()['rows'] if r['kind']=='BATCH' and r['id']==batch)
    assert row['available']=='1000'


def test_competing_transforms_cannot_consume_same_intake_twice(setup):
    app,client,base,evidence,facility,batch=setup
    entity=intake(client,base,facility,evidence,'intake-1')
    def post(key):
        return client.post(base+'/transforms',json=transform(facility,evidence,[parent('INTAKE',entity,'100')],external_id=key)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post,['compete-a','compete-b']))==[200,409]
