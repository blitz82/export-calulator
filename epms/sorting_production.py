"""Append-only production DAG sharing the basic allocation ledger."""
from epms import sorting as s, store, rules


def production_rows(conn,org,*kinds):
    return [dict(row) for row in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind.in_(kinds)).order_by(s.records.c.id)).mappings()]


def replay(conn,org,kind,data):
    for row in production_rows(conn,org,kind):
        if row['kind']==kind and row['data']['external_id']==data['external_id']:
            original={key:row['data'].get(key) for key in data}
            if original!=data:
                s.reject('SORT_E09','external_id','동일 원본번호의 내용이 다릅니다. 새 번호로 등록하세요.')
            return row
    return None


def transfers(conn,org,kind,entity_id):
    total=0
    for row in production_rows(conn,org,'LOT_TRANSFORM'):
        if row['kind']=='LOT_TRANSFORM':
            total+=sum(s.kg(p['quantity']) for p in row['data']['inputs'] if p['kind']==kind and p['id']==entity_id)
    return total


def batch_committed(conn,org,batch_id):
    from epms.settlement import used_batch
    reservations=sum(row['quantity'] for row in s.list_rows(conn,s.allocations,org)
                     if row['batch']==batch_id and row['active'] and not row['data'].get('dispatch_id'))
    dispatches=sum(s.kg(row['data']['quantity']) for row in production_rows(conn,org,'DISPATCH')
                   if row['kind']=='DISPATCH' and row['data']['batch_id']==batch_id)
    return reservations+dispatches+transfers(conn,org,'BATCH',batch_id)+used_batch(conn,batch_id)


def intake(conn,org,data,actor):
    facility=s.scoped(conn,s.facilities,org,data['facility_id'])
    if facility['data']['product']!=data['product']:
        s.reject('SORT_E09','product','입고 품목과 시설 품목이 다릅니다.',422)
    s.scoped(conn,s.evidence,org,data['evidence_id'])
    if data['unit']!='kg' or s.kg(data['quantity'])<=0 or not rules.day(data['date']):
        s.reject('SORT_E03','quantity','검증된 kg 입고량과 입고일을 입력하세요.',422)
    previous=conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='INTAKE',
        s.records.c.data['external_id'].as_string()==data['external_id']).order_by(s.records.c.id)).mappings().first()
    if previous:
        if previous['data'].get('input_hash')==s.digest(data):
            return previous['id']
        s.reject('SORT_E09','external_id','중복 입고 ID의 내용이 다릅니다.')
    value=dict(data,input_hash=s.digest(data),original_unit=data['unit'],producer_id=data['producer_id'])
    entity_id=conn.execute(s.records.insert().values(org=org,kind='INTAKE',revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_intake',entity_id,'입고 검토 등록',None,dict(quantity=data['quantity']))
    return entity_id


def transform(conn,org,data,actor):
    facility=s.scoped(conn,s.facilities,org,data['facility_id'])
    if facility['data']['product']!=data['product']:
        s.reject('SORT_E09','product','작업 품목과 시설 품목이 다릅니다.',422)
    s.scoped(conn,s.evidence,org,data['evidence_id'])
    previous=replay(conn,org,'LOT_TRANSFORM',data)
    if previous:
        return dict(id=previous['id'],batch_id=previous['data']['output_batch'])
    if not rules.day(data['date']):
        s.reject('SORT_E09','date','작업일을 확인하세요.',422)
    if not data['inputs'] or len(data['inputs'])>1000:
        s.reject('SORT_E03','inputs','입고 또는 이전 작업을 선택하세요.',422)
    consumed={};group_consumed={}
    for parent in data['inputs']:
        quantity=s.kg(parent['quantity'])
        key=(parent['kind'],parent['id'])
        consumed[key]=consumed.get(key,0)+quantity
        if parent['kind']=='INTAKE':
            source=s.scoped(conn,s.records,org,parent['id'])
            if source['kind']!='INTAKE':
                s.reject('SORT_E03','inputs','입고 기록을 선택하세요.',422)
            maximum=s.kg(source['data']['quantity'])
            used=transfers(conn,org,'INTAKE',source['id'])
        elif parent['kind']=='BATCH':
            source=s.scoped(conn,s.batches,org,parent['id'])
            maximum=source['quantity']
            used=batch_committed(conn,org,source['id'])
            from epms.settlement import group_remaining
            root,shared=group_remaining(conn,org,source['id'])
            group_consumed[root]=group_consumed.get(root,0)+quantity
            if group_consumed[root]>shared:s.reject('SORT_E03','inputs','원집계·분할 기록의 공통 잔량을 초과합니다.')
        else:
            s.reject('SORT_E03','inputs','원물 종류를 확인하세요.',422)
        if source['data']['product']!=data['product'] or source['data']['facility_id']!=data['facility_id']:
            s.reject('SORT_E09','inputs','원물의 시설·품목이 다릅니다.')
        source_date=source['data']['date'] if parent['kind']=='INTAKE' else source['data']['work_date']
        if data['date']<source_date:
            s.reject('SORT_E03','date','작업일이 원물 입고일·선행 작업일보다 빠릅니다.',422)
        if quantity<=0 or used+consumed[key]>maximum:
            s.reject('SORT_E03','inputs','원물 잔량 또는 기존 예약량을 초과합니다.')
    outputs={key:s.kg(data[key]) for key in ('export_quantity','domestic_quantity','reject_quantity','loss_quantity','inventory_quantity')}
    if sum(outputs.values())!=sum(consumed.values()):
        s.reject('SORT_E03','outputs','원물량과 적합·내수·비상품·감모·재고 합계가 다릅니다.')
    if not rules.day(data['date']):
        s.reject('SORT_E09','date','작업일을 확인하세요.',422)
    # Only new output nodes can reference existing parents: cycles cannot be introduced.
    output_batch=None
    if outputs['export_quantity']:
        output_batch=s.add_batch(conn,org,dict(facility_id=data['facility_id'],external_id=data['external_id'],
            work_date=data['date'],product=data['product'],quantity=data['export_quantity'],unit='kg',
            evidence_id=data['evidence_id'],production=True),actor)
    value=dict(data,output_batch=output_batch,inventory_reuse='POLICY_PENDING')
    entity_id=conn.execute(s.records.insert().values(org=org,kind='LOT_TRANSFORM',revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_transform',entity_id,'선별·재선별 물량수지',None,dict(output_batch=output_batch))
    return dict(id=entity_id,batch_id=output_batch)


def dispatch(conn,org,data,actor):
    batch=s.scoped(conn,s.batches,org,data['batch_id'])
    s.scoped(conn,s.evidence,org,data['evidence_id'])
    previous=replay(conn,org,'DISPATCH',data)
    if previous:
        return previous['id']
    quantity=s.kg(data['quantity'])
    from epms.settlement import group_remaining
    _,shared=group_remaining(conn,org,batch['id'])
    if quantity>shared:s.reject('SORT_E03','quantity','원집계·분할 기록의 공통 잔량을 초과합니다.')
    if quantity<=0 or batch_committed(conn,org,batch['id'])+quantity>batch['quantity']:
        s.reject('SORT_E03','quantity','출고 가능 잔량을 초과합니다.')
    if not rules.day(data['date']) or data['date']<batch['data']['work_date']:
        s.reject('SORT_E03','date','출고일과 선별일 순서를 확인하세요.',422)
    for row in production_rows(conn,org,'DISPATCH'):
        if row['kind']=='DISPATCH' and row['data']['external_id']==data['external_id']:
            s.reject('SORT_E09','external_id','동일 출고번호가 있습니다.')
    entity_id=conn.execute(s.records.insert().values(org=org,kind='DISPATCH',revision=1,data=data)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_dispatch',entity_id,'출고 등록',None,dict(batch_id=batch['id'],quantity=data['quantity']))
    return entity_id


def ledger(conn,org):
    """Review balances only; dispatch commitments include their reservations once."""
    records=production_rows(conn,org,'INTAKE','LOT_TRANSFORM','DISPATCH')
    allocations=s.list_rows(conn,s.allocations,org)
    transformed={}
    dispatched={}
    reserved={}
    for row in records:
        if row['kind']=='LOT_TRANSFORM':
            for parent in row['data']['inputs']:
                key=(parent['kind'],parent['id'])
                transformed[key]=transformed.get(key,0)+s.kg(parent['quantity'])
        if row['kind']=='DISPATCH':
            batch_id=row['data']['batch_id']
            dispatched[batch_id]=dispatched.get(batch_id,0)+s.kg(row['data']['quantity'])
    for row in allocations:
        if row['active'] and not row['data'].get('dispatch_id'):
            reserved[row['batch']]=reserved.get(row['batch'],0)+row['quantity']
    rows=[]
    sources=[('INTAKE',r) for r in records if r['kind']=='INTAKE']
    sources += [('BATCH',r) for r in s.list_rows(conn,s.batches,org)]
    for kind,row in sources:
        value=row['data'];total=s.kg(value['quantity']) if kind=='INTAKE' else row['quantity']
        used=transformed.get((kind,row['id']),0)
        outgoing=dispatched.get(row['id'],0) if kind=='BATCH' else 0
        held=reserved.get(row['id'],0) if kind=='BATCH' else 0
        available=total-used-outgoing-held
        rows.append(dict(kind=kind,id=row['id'],revision=row['revision'],facility_id=value['facility_id'],product=value['product'],
            external_id=value['external_id'],date=value['date'] if kind=='INTAKE' else value['work_date'],evidence_id=value['evidence_id'],
            quantity=s.text_quantity(total),transformed=s.text_quantity(used),dispatched=s.text_quantity(outgoing),reserved=s.text_quantity(held),
            available=s.text_quantity(available),integrity='OK' if available>=0 else 'CONFLICT',physical_trace_verified=False))
    return dict(rows=rows,unit='kg',label='등록 원장 잔량 / 실제 원물 검증 아님',inventory_reuse='POLICY_PENDING',
                definition='등록량 - 후속 작업 투입 - 출고량 - 출고를 통하지 않은 활성 예약량; 출고분 예약은 재차 차감하지 않음')
