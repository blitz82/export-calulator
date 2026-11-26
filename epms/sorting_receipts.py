"""Facility/period intake groups referencing the existing physical source and cost rows."""
from pydantic import Field
from sqlalchemy import Column,ForeignKey,Integer,JSON,String,UniqueConstraint,select,or_
from epms import sorting as s,store,rules,settlement as st

receipts=s.table('receipts',Column('facility',ForeignKey(s.facilities.c.id),nullable=False),Column('revision',Integer,nullable=False),Column('create_key',String,nullable=False),Column('data',JSON,nullable=False),UniqueConstraint('org','create_key'))

class Document(st.EvidenceRef):
    kind:str=Field(default='선별기록',max_length=100)
    relation:str=Field(default='원본',pattern='^(원본|보조 증빙|정정본)$')
    replaces_file:int|None=None
    note:str=Field(default='',max_length=1000)

class Receipt(st.Strict):
    revision:int|None=None
    request_key:str=Field(min_length=8,max_length=100)
    facility_id:int
    title:str=Field(min_length=1,max_length=200)
    period_type:str=Field(default='월별',pattern='^(일별|월별)$')
    start:str=''
    end:str=''
    received:str
    kind:str=Field(default='최초 접수',pattern='^(최초 접수|추가 제출|정정 제출)$')
    parent_id:int|None=None
    documents:list[Document]=Field(default_factory=list,max_length=200)
    needs_supplement:bool=False
    supplement_note:str=Field(default='',max_length=2000)
    memo:str=Field(default='',max_length=2000)
    reason:str=Field(default='',max_length=1000)

class Attach(st.Strict):
    revision:int
    kind:str=Field(pattern='^(source|cost)$')
    target:int
    target_revision:int
    reason:str=Field(min_length=1,max_length=1000)

def ids(data):
    return set(data.get('receipt_ids',[]))|({data['receipt_id']} if data.get('receipt_id') else set())

def bind(conn,org,facility,receipt_id,old,evidence):
    previous=ids(old or {})
    receipt_id=receipt_id or (old or {}).get('receipt_id')
    if receipt_id:
        r=s.scoped(conn,receipts,org,receipt_id)
        if r['facility']!=facility:st.fail('접수 묶음과 기록의 공동선별장이 다릅니다.')
        allowed={v['file'] for v in r['data'].get('documents',[])}
        if any(v['file'] not in allowed for v in evidence):st.fail('사용할 PDF를 먼저 이 접수 묶음의 첨부자료에 추가하세요.')
        previous.add(receipt_id)
    return dict(receipt_id=receipt_id,receipt_ids=sorted(previous))

def blocked(conn,org,data):
    key=data.get('receipt_id')
    if not key:return False
    return bool(s.scoped(conn,receipts,org,key)['data'].get('needs_supplement'))

def affected(conn,org,kind,key):
    result=[]
    for job in s.list_rows(conn,st.jobs,org):
        entries=job['data'].get('lines' if kind=='source' else 'costs',[])
        if any(v.get('batch_id' if kind=='source' else 'expense_id')==key for v in entries):
            result.append(dict(id=job['id'],title=job['data']['title'],state=job['state']))
    return result

def save(conn,org,value,user,key=None):
    d=value.model_dump(exclude={'revision','request_key'});s.scoped(conn,s.facilities,org,value.facility_id)
    if not rules.day(value.received):st.fail('자료 접수일을 확인하세요.')
    if value.start or value.end:d['start'],d['end']=st.period(value.start,value.end)
    if value.period_type=='일별' and value.start!=value.end:st.fail('일별 접수는 같은 시작일·종료일을 지정하세요.')
    if value.needs_supplement and not value.supplement_note:st.fail('보완이 필요한 내용을 입력하세요.')
    if value.kind!='최초 접수' and (not value.parent_id or not value.reason):st.fail('추가·정정 제출은 기존 접수 묶음과 사유를 지정하세요.')
    if value.parent_id:
        parent=s.scoped(conn,receipts,org,value.parent_id)
        if parent['facility']!=value.facility_id or parent['id']==key:st.fail('같은 선별장의 다른 접수 묶음을 선택하세요.')
        seen={key}
        while parent:
            if parent['id'] in seen:st.fail('접수 묶음의 순환 참조는 허용하지 않습니다.')
            seen.add(parent['id']);p=parent['data'].get('parent_id');parent=s.scoped(conn,receipts,org,p) if p else None
    seen_files=set()
    for doc in value.documents:
        st.refs(conn,org,[doc.model_dump(include={'file','page'})])
        if doc.file in seen_files:st.fail('동일 PDF는 접수 묶음에 한 번만 첨부하세요.')
        seen_files.add(doc.file)
        if doc.relation=='정정본':
            if not doc.replaces_file or not doc.note or doc.replaces_file==doc.file:st.fail('정정본은 이전 PDF와 정정 내용을 지정하세요.')
            s.scoped(conn,s.evidence,org,doc.replaces_file)
    old=s.scoped(conn,receipts,org,key) if key else None
    if old:
        if value.revision!=old['revision']:st.fail('접수 묶음이 변경되었습니다. 새로 조회하세요.',409)
        if old['facility']!=value.facility_id:st.fail('기존 접수 묶음의 선별장은 변경할 수 없습니다.')
        if not value.reason:st.fail('변경 사유를 입력하세요.')
        for table in (s.batches,s.expenses):
            for row in s.list_rows(conn,table,org):
                if key in ids(row['data']) and any(v['file'] not in seen_files for v in row['data'].get('evidence',[])):
                    st.fail('이 접수에 연결된 기록이 사용하는 PDF는 제거할 수 없습니다. 정정본을 추가하세요.')
    else:
        existing=conn.execute(receipts.select().where(receipts.c.org==org,receipts.c.create_key==value.request_key)).mappings().first()
        if existing:
            if existing['data']!=d:st.fail('동일 접수 요청의 내용이 다릅니다.',409)
            return dict(existing)
    revision=old['revision']+1 if old else 1
    values=dict(org=org,facility=value.facility_id,revision=revision,data=d)
    if old:conn.execute(receipts.update().where(receipts.c.id==key).values(**values))
    else:key=conn.execute(receipts.insert().values(**values,create_key=value.request_key)).inserted_primary_key[0]
    store.log(conn,org,user,'sorting_receipt',key,value.reason or '선별장 자료 접수',old,dict(values,id=key))
    return dict(values,id=key)

def summary(conn,org,row,batches,expenses):
    from epms.sorting_production import batch_committed
    key=row['id'];sources=[b for b in batches if key in ids(b['data'])];costs=[c for c in expenses if key in ids(c['data'])]
    issues=[];unknown=False;total=st.Decimal(0);counted=set()
    by_id={b['id']:b for b in batches}
    for b in sources:
        d=b['data'];children=[c for c in sources if c['data'].get('parent_id')==b['id']]
        reviewed=st.source_issues(d)
        if d.get('start') and row['data']['start'] and (d['start']<row['data']['start'] or d.get('end','')>row['data']['end']):reviewed.append('선별기간과 접수 대상 기간이 다릅니다.')
        if children and not d.get('producer') and all(not st.source_issues(c['data']) for c in children) and sum(c['quantity'] for c in children)==b['quantity']:
            reviewed=[v for v in reviewed if v!='생산자 미확인']
        issues.extend(f"{d.get('title','기록')}: {v}" for v in reviewed)
        if d.get('quantity_unknown'):unknown=True
        root=d.get('parent_id') or b['id']
        if root not in counted and root in {x['id'] for x in sources}:total+=st.Decimal(by_id[root]['quantity'])/1_000_000;counted.add(root)
        elif root not in {x['id'] for x in sources}:total+=st.Decimal(b['quantity'])/1_000_000
        if children and sum(c['quantity'] for c in children)>b['quantity']:issues.append(f"{d.get('title')}: 분할 합계가 원집계를 초과합니다.")
    for c in costs:
        issues.extend(f"{c['data']['title']}: {v}" for v in st.expense_issues(c['data']))
        if c['data'].get('start') and row['data']['start'] and (c['data']['start']<row['data']['start'] or c['data'].get('end','')>row['data']['end']):issues.append(c['data']['title']+': 비용기간과 접수 대상 기간이 다릅니다.')
    if not row['data']['documents']:issues.append('첨부 PDF 없음')
    if not row['data']['start'] or not row['data']['end']:issues.append('대상 기간 미확인')
    if row['data'].get('needs_supplement'):issues.append(row['data']['supplement_note'])
    state='연결 준비 완료' if sources and not issues else '보완 필요' if row['data'].get('needs_supplement') else '입력 중' if sources or costs else '접수'
    source_ids={b['id'] for b in sources}
    used=sum(batch_committed(conn,org,b['id']) for b in batches if b['id'] in source_ids or b['data'].get('parent_id') in source_ids)
    # Parent/child pools are counted once; source usage is actual edge consumption.
    connection='미연결' if used==0 else '전량 연결' if not unknown and st.Decimal(used)/1_000_000>=total else '일부 연결'
    return dict(row,state=state,source_count=len(sources),cost_count=len(costs),export_quantity=None if unknown else str(total),linked_quantity=s.text_quantity(used),connection=connection,issues=list(dict.fromkeys(issues)))

def install(app,db,gate,actor):
    from fastapi import Request
    prefix='/api/org/{org}/sorting-receipts'
    @app.get(prefix)
    def listing(org:int):
        with db.connect() as conn:
            gate(conn,org);bs=s.list_rows(conn,s.batches,org);cs=s.list_rows(conn,s.expenses,org)
            return [summary(conn,org,r,bs,cs) for r in s.list_rows(conn,receipts,org)][::-1]
    @app.post(prefix)
    def create(org:int,value:Receipt,request:Request):
        with db.begin() as conn:conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return save(conn,org,value,actor(request))
    @app.put(prefix+'/{key}')
    def update(org:int,key:int,value:Receipt,request:Request):
        with db.begin() as conn:conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return save(conn,org,value,actor(request),key)
    @app.get(prefix+'/{key}/history')
    def history(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,receipts,org,key)
            bs=[b['id'] for b in s.list_rows(conn,s.batches,org) if key in ids(b['data'])];cs=[c['id'] for c in s.list_rows(conn,s.expenses,org) if key in ids(c['data'])]
            return [dict(r) for r in conn.execute(store.audit.select().where(store.audit.c.org==org,or_((store.audit.c.entity=='sorting_receipt')&(store.audit.c.target==key),(store.audit.c.entity=='settlement_source')&store.audit.c.target.in_(bs),(store.audit.c.entity=='settlement_expense')&store.audit.c.target.in_(cs))).order_by(store.audit.c.id.desc())).mappings()]
    @app.post(prefix+'/{key}/attach')
    def attach(org:int,key:int,value:Attach,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);receipt=s.scoped(conn,receipts,org,key)
            if receipt['revision']!=value.revision:st.fail('접수 묶음이 변경되었습니다.',409)
            table=s.batches if value.kind=='source' else s.expenses;row=s.scoped(conn,table,org,value.target);old=row['data'];revision=row.get('revision',old.get('revision',1))
            if revision!=value.target_revision:st.fail('기존 기록이 변경되었습니다.',409)
            if row['facility']!=receipt['facility']:st.fail('다른 선별장의 기록입니다.')
            if key in ids(old):return dict(id=key,revision=receipt['revision'])
            d=dict(receipt['data']);docs=list(d['documents']);seen={v['file'] for v in docs}
            evidence=old.get('evidence') or ([dict(file=old['evidence_id'],page=1)] if old.get('evidence_id') else [])
            for ref in evidence:
                st.refs(conn,org,[ref])
                if ref['file'] not in seen:docs.append(dict(ref,kind='기존 기록 근거',relation='원본',note='',replaces_file=None));seen.add(ref['file'])
            d['documents']=docs
            conn.execute(receipts.update().where(receipts.c.id==key).values(revision=receipt['revision']+1,data=d))
            data=dict(old,**bind(conn,org,row['facility'],key,old,evidence))
            values=dict(data=data)
            if value.kind=='source':values['revision']=revision+1
            else:data['revision']=revision+1
            conn.execute(table.update().where(table.c.id==row['id']).values(**values))
            user=actor(request);store.log(conn,org,user,'sorting_receipt',key,value.reason,receipt,dict(d,revision=receipt['revision']+1))
            store.log(conn,org,user,'settlement_source' if value.kind=='source' else 'settlement_expense',row['id'],value.reason,row,dict(values,id=row['id']))
            return dict(id=key,revision=receipt['revision']+1,affected_jobs=affected(conn,org,value.kind,row['id']))
