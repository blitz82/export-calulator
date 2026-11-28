"""Saved many-to-many sorting settlements with independent physical balances."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, localcontext
from datetime import date
from contextlib import closing
from hashlib import sha256
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, ForeignKey, Integer, JSON, LargeBinary, String, Table, UniqueConstraint, select, func, or_
from epms import store, rules, sorting as s, contract_gate, ocr

jobs = s.table('settlement_jobs', Column('revision',Integer,nullable=False), Column('state',String,nullable=False),
    Column('create_key',String,nullable=False), Column('data',JSON,nullable=False), UniqueConstraint('org','create_key'))
links = s.table('settlement_links', Column('job',ForeignKey(jobs.c.id),nullable=False),
    Column('batch',ForeignKey(s.batches.c.id),nullable=False), Column('declaration',ForeignKey(store.declarations.c.id),nullable=False),
    Column('logical_key',String,nullable=False), Column('left',Integer,nullable=False), Column('right',Integer,nullable=False),
    Column('active',Integer,nullable=False), Column('data',JSON,nullable=False))
cost_links = s.table('settlement_cost_links', Column('job',ForeignKey(jobs.c.id),nullable=False),
    Column('expense',ForeignKey(s.expenses.c.id),nullable=False), Column('amount',String,nullable=False),Column('active',Integer,nullable=False))
snapshots = s.table('settlement_snapshots', Column('job',ForeignKey(jobs.c.id),nullable=False),
    Column('revision',Integer,nullable=False), Column('request_key',String,nullable=False),Column('input_hash',String,nullable=False),
    Column('data',JSON,nullable=False),Column('pdf',LargeBinary), UniqueConstraint('job','request_key'))
TABLES=(jobs,links,cost_links,snapshots)

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class EvidenceRef(Strict):
    file:int=Field(ge=1)
    page:int=Field(ge=1,le=150)

class Source(Strict):
    revision:int|None=None
    facility_id:int
    title:str=Field(min_length=1,max_length=200)
    external_id:str=Field(min_length=1,max_length=150)
    period_type:str=Field(pattern='^(일별|월별)$')
    start:str
    end:str
    producer_id:int|None=None
    parent_id:int|None=None
    receipt_id:int|None=None
    field_evidence:dict[str,EvidenceRef]=Field(default_factory=dict,max_length=20)
    change_reason:str=Field(default='',max_length=1000)
    export_quantity:str
    total:str=''
    domestic:str=''
    rejected:str=''
    loss:str=''
    unit:str=Field(default='kg',pattern='^(kg|t)$')
    evidence:list[EvidenceRef]=Field(default_factory=list,max_length=100)
    confirmed:bool=False
    overlap_reason:str=Field(default='',max_length=1000)
    memo:str=Field(default='',max_length=2000)

class Payment(Strict):
    reference:str=Field(min_length=1,max_length=100)
    date:str
    amount:str
    refund:bool=False

class Expense(Strict):
    revision:int|None=None
    facility_id:int
    receipt_id:int|None=None
    change_reason:str=Field(default='',max_length=1000)
    field_evidence:dict[str,EvidenceRef]=Field(default_factory=dict,max_length=20)
    title:str=Field(min_length=1,max_length=200)
    external_id:str=Field(min_length=1,max_length=150)
    start:str
    end:str
    category:str=Field(default='선별 인건비',max_length=100)
    gross:str
    payments:list[Payment]=Field(default_factory=list,max_length=100)
    excluded:str='0'
    exclusion_reason:str=Field(default='',max_length=1000)
    evidence:list[EvidenceRef]=Field(default_factory=list,max_length=100)
    batch_ids:list[int]=Field(default_factory=list,max_length=1000)
    confirmed:bool=False

class Edge(Strict):
    batch_id:int
    batch_revision:int
    declaration_id:int
    declaration_revision:int
    left:str
    right:str
    reason:str=Field(default='',max_length=1000)
    exception_reason:str=Field(default='',max_length=1000)
    period_reason:str=Field(default='',max_length=1000)
    evidence:list[EvidenceRef]=Field(default_factory=list,max_length=100)

class CostShare(Strict):
    expense_id:int
    revision:int
    method:str=Field(pattern='^(직접 확인액|작업시간 비율|물량 비율|수동 배부액)$')
    amount:str=''
    numerator:str=''
    denominator:str=''
    reason:str=Field(min_length=1,max_length=1000)

class Policy(Strict):
    label:str=Field(default='가정값에 따른 참고 계산',min_length=1,max_length=200)
    reference:str=Field(default='',max_length=2000)
    formula:str=Field(default='물량 단가',pattern='^(물량 단가|실비 한도)$')
    unit_price:str=''
    rate:str=''
    rate_included:bool=False
    cap:str=''
    cap_quantity:str=''
    scope_cap:str=''
    deduction:str='0'
    tolerance_kg:str=''
    tolerance_percent:str=''
    tolerance_rule:str=Field(default='둘 다',pattern='^(둘 다|하나 이상)$')
    rounding:str=Field(default='원 미만 절사',pattern='^(원 미만 절사|원 단위 반올림)$')
    assumptions_confirmed:bool=False

class MemberCost(Strict):
    member_id:int=Field(ge=1)
    amount:str=Field(max_length=40)

class Job(Strict):
    revision:int|None=None
    request_key:str=Field(min_length=8,max_length=100)
    title:str=Field(min_length=1,max_length=200)
    year:int=Field(ge=1900,le=9999)
    facility_id:int
    start:str
    end:str
    policy:Policy=Field(default_factory=Policy)
    lines:list[Edge]=Field(default_factory=list,max_length=1000)
    costs:list[CostShare]=Field(default_factory=list,max_length=1000)
    memo:str=Field(default='',max_length=2000)
    support_revision:int|None=Field(default=None,ge=1)
    member_costs:list[MemberCost]=Field(default_factory=list,max_length=10000)

def fail(text,status=422):
    raise HTTPException(status,text)

def number(value,optional=False):
    if optional and value=='':return None
    result=rules.decimal(value)
    if result is None or result<0 or abs(result.as_tuple().exponent)>6:
        fail('물량·금액은 0 이상의 숫자로 소수점 6자리까지 입력하세요.')
    return result

def scaled(value):
    return s.kg(format(number(value),'f'))

def period(start,end):
    a,b=rules.day(start),rules.day(end)
    if not a or not b or a>b:fail('대상 기간의 시작일과 종료일을 확인하세요.')
    return a.isoformat(),b.isoformat()

def refs(conn,org,values):
    result=[]
    for v in values:
        d=v.model_dump() if isinstance(v,BaseModel) else v
        row=s.scoped(conn,s.evidence,org,d['file'])
        if sha256(row['content']).hexdigest()!=row['sha']:fail('증빙 원본 해시가 변경되었습니다. 원본을 다시 확인하세요.',409)
        try:
            with ocr.PDF_LOCK,ocr.open_pdf(row['content']) as doc:
                if not 1<=d['page']<=len(doc):fail('증빙 PDF 페이지 범위를 확인하세요.')
        except ValueError as error:fail(str(error))
        result.append(dict(d,name=row['name'],sha=row['sha']))
    return result

def used_declaration(conn,key,exclude=None):
    query=select(func.coalesce(func.sum(links.c.right),0)).where(links.c.logical_key==key,links.c.active==1)
    if exclude:query=query.where(links.c.job!=exclude)
    return conn.execute(query).scalar_one()

def used_batch(conn,batch,exclude=None):
    query=select(func.coalesce(func.sum(links.c.left),0)).where(links.c.batch==batch,links.c.active==1)
    if exclude:query=query.where(links.c.job!=exclude)
    return conn.execute(query).scalar_one()

def group_remaining(conn,org,batch_id,exclude=None):
    from epms.sorting_production import batch_committed
    batch=s.scoped(conn,s.batches,org,batch_id)
    root_id=batch['data'].get('parent_id') or batch_id
    root=s.scoped(conn,s.batches,org,root_id)
    children=[dict(b) for b in conn.execute(s.batches.select().where(s.batches.c.org==org,or_(s.batches.c.id==root_id,s.batches.c.data['parent_id'].as_integer()==root_id))).mappings()]
    committed=sum(batch_committed(conn,org,b['id'])-used_batch(conn,b['id'])+used_batch(conn,b['id'],exclude) for b in children)
    return root_id,root['quantity']-committed

def paid(data):
    return sum((number(p['amount'])*(-1 if p.get('refund') else 1) for p in data['payments']),Decimal(0))

def source_issues(d):
    issues=[]
    if not d.get('confirmed'):issues.append('원문 대조 미완료')
    if not d.get('producer'):issues.append('생산자 미확인')
    if d.get('quantity_unknown'):issues.append('수출용 물량 미확인')
    if not rules.day(d.get('start',d.get('work_date'))) or not rules.day(d.get('end',d.get('work_date'))):issues.append('선별 기간 미확인')
    if not d.get('evidence'):issues.append('근거 PDF 없음')
    if d.get('warnings') and not d.get('overlap_reason'):issues.append('합계·중복기간 확인 사유 필요')
    return issues

def expense_issues(d):
    issues=[]
    if not d.get('confirmed'):issues.append('지급 원문 대조 미완료')
    if d.get('gross','')=='':issues.append('발생액 미확인')
    if not rules.day(d.get('start')) or not rules.day(d.get('end')):issues.append('비용 기간 미확인')
    if not d.get('evidence'):issues.append('근거 PDF 없음')
    if not d.get('payments'):issues.append('지급 내역 미입력')
    if not d.get('batch_ids'):issues.append('관련 선별기록 미지정')
    return issues

def used_cost(conn,expense,exclude=None):
    query=cost_links.select().where(cost_links.c.expense==expense,cost_links.c.active==1)
    if exclude:query=query.where(cost_links.c.job!=exclude)
    current=sum((number(r.amount) for r in conn.execute(query)),Decimal(0))
    legacy=sum((number(r.amount) for r in conn.execute(s.expense_allocations.select().where(s.expense_allocations.c.expense==expense,s.expense_allocations.c.active==1))),Decimal(0))
    return current+legacy

def source_save(conn,org,value,user,key=None):
    from epms import sorting_receipts as intake
    d=value.model_dump(exclude={'revision'})
    if d['start'] or d['end']:d['start'],d['end']=period(d['start'],d['end'])
    facility=s.scoped(conn,s.facilities,org,value.facility_id)
    if d['period_type']=='일별' and d['start']!=d['end']:fail('일별 기록은 같은 날짜를 지정하세요.')
    if value.producer_id:
        m=s.scoped(conn,store.memberships,org,value.producer_id)
        if m['data'].get('kind') not in {'생산자','복수'}:fail('생산자회원사를 선택하세요.')
        d['producer']=dict(id=m['id'],name=m['data']['name'],number=m['number'])
    else:d['producer']=None
    d['evidence_refs']=refs(conn,org,value.evidence)
    multiplier=Decimal(1000) if value.unit=='t' else Decimal(1)
    unknown=value.export_quantity==''
    q=Decimal(0) if unknown else number(value.export_quantity)*multiplier
    if value.parent_id:
        parent=s.scoped(conn,s.batches,org,value.parent_id)
        if parent['facility']!=value.facility_id or parent['id']==key or parent['data'].get('parent_id'):fail('같은 시설의 원집계 기록을 선택하세요. 자기 자신·재귀 분할은 허용하지 않습니다.')
        pd=parent['data']
        if not pd.get('start',pd.get('work_date',''))<=d['start']<=d['end']<=pd.get('end',pd.get('work_date','')):fail('분할 기록의 기간은 원집계 대상 기간 안이어야 합니다.')
        if pd.get('producer') and d.get('producer') and pd['producer']['id']!=d['producer']['id']:fail('생산자가 확인된 원집계를 다른 생산자의 물량으로 바꿀 수 없습니다.')
        if key and any(b['data'].get('parent_id')==key for b in s.list_rows(conn,s.batches,org)):fail('이미 분할 기록이 있는 원집계를 다른 기록의 하위로 옮길 수 없습니다.')
        if s.kg(str(q))>parent['quantity']:fail('분할 물량이 원집계의 수출량을 초과합니다.')
    parts={k:None if getattr(value,k)=='' else str(number(getattr(value,k))*multiplier) for k in ('total','domestic','rejected','loss')}
    warnings=[]
    if parts['total'] is not None and q>Decimal(parts['total']):warnings.append('수출물량이 기록된 전체 투입물량보다 큽니다. 원본·분류를 보완하세요.')
    if parts['total'] is not None and all(parts[k] is not None for k in ('domestic','rejected','loss')):
        delta=Decimal(parts['total'])-q-sum((Decimal(parts[k]) for k in ('domestic','rejected','loss')),Decimal(0))
        if delta:warnings.append(f'총물량과 구분별 합계 차이 {delta} kg')
    d.update(parts,quantity=str(q),export_quantity='' if unknown else str(q),quantity_unknown=unknown,unit='kg',original_unit=value.unit,original_export_quantity=value.export_quantity,
        original_values={k:getattr(value,k) for k in ('export_quantity','total','domestic','rejected','loss')},
        work_date=d['start'],product=conn.execute(select(store.organizations.c.name).where(store.organizations.c.id==org)).scalar_one(),
        evidence_id=value.evidence[0].file if value.evidence else None,warnings=warnings,settlement=True)
    for field,ref in value.field_evidence.items():
        if field not in {'start','end','export_quantity','total','domestic','rejected','loss'}:fail('입력 항목별 근거를 확인하세요.')
        refs(conn,org,[ref])
        if ref.file not in {e.file for e in value.evidence}:fail('항목 근거 PDF를 기록의 근거에도 추가하세요.')
    for other in s.list_rows(conn,s.batches,org):
        if other['id']!=key and other['facility']==value.facility_id and other['data'].get('external_id')==value.external_id:
            fail('같은 원본 기록번호가 있습니다. 기존 기록을 열어 수정하세요.',409)
    old=s.scoped(conn,s.batches,org,key) if key else None
    if old and old['revision']!=value.revision:fail('선별기록이 변경되었습니다. 다시 조회하세요.',409)
    if old and old['facility']!=value.facility_id:fail('기존 선별기록의 시설을 변경할 수 없습니다.')
    if old and old['data'].get('parent_id')!=value.parent_id:fail('기존 선별기록의 원집계 연결은 변경하지 않습니다. 별도 분할 기록으로 등록하세요.')
    d.update(intake.bind(conn,org,value.facility_id,value.receipt_id,old['data'] if old else None,d['evidence']))
    if old and (old['data'].get('receipt_id') or value.receipt_id) and not value.change_reason:fail('기록 변경 사유를 입력하세요.')
    revision=old['revision']+1 if old else 1
    values=dict(org=org,facility=value.facility_id,revision=revision,quantity=s.kg(str(q)),data=d)
    if old:conn.execute(s.batches.update().where(s.batches.c.id==key).values(**values))
    else:key=conn.execute(s.batches.insert().values(**values)).inserted_primary_key[0]
    store.log(conn,org,user,'settlement_source',key,value.change_reason or '선별기록 확인·저장',old,dict(values,id=key))
    return dict(id=key,revision=revision,warnings=warnings,issues=source_issues(d),affected_jobs=intake.affected(conn,org,'source',key))

def expense_save(conn,org,value,user,key=None):
    from epms import sorting_receipts as intake
    d=value.model_dump(exclude={'revision'})
    if d['start'] or d['end']:d['start'],d['end']=period(d['start'],d['end'])
    s.scoped(conn,s.facilities,org,value.facility_id)
    d['evidence_refs']=refs(conn,org,value.evidence)
    for field,ref in value.field_evidence.items():
        if field not in {'gross','start','end','excluded'}:fail('비용 항목 근거를 확인하세요.')
        refs(conn,org,[ref])
        if ref.file not in {v.file for v in value.evidence}:fail('비용 항목 근거를 비용 PDF에도 추가하세요.')
    gross=number(value.gross,True);ex=number(value.excluded)
    seen=set()
    for p in d['payments']:
        if not rules.day(p['date']):fail('지급·환입 일자를 확인하세요.')
        number(p['amount'])
        if p['reference'] in seen:fail('같은 지급 거래번호를 중복 입력할 수 없습니다.')
        seen.add(p['reference'])
    if paid(d)<0 or (gross is not None and paid(d)>gross) or ex>paid(d):fail('지급·환입·제외액은 발생액과 순지급액 범위 안이어야 합니다.')
    if ex and not value.exclusion_reason:fail('제외 비용의 사유를 입력하세요.')
    for bid in value.batch_ids:
        if s.scoped(conn,s.batches,org,bid)['facility']!=value.facility_id:fail('관련 선별기록의 시설이 다릅니다.')
    for other in s.list_rows(conn,s.expenses,org):
        if other['id']!=key and other['data'].get('external_id')==value.external_id:fail('같은 비용 사건 번호가 있습니다. 증빙을 기존 비용에 추가하세요.',409)
        if other['id']!=key and other['facility']==value.facility_id and any(p['reference'] in seen for p in other['data'].get('payments',[])):
            fail('같은 지급 거래가 다른 비용 사건에 등록되어 있습니다. 한 비용 사건에 증빙을 모아 중복 합산을 막으세요.',409)
    old=s.scoped(conn,s.expenses,org,key) if key else None
    if old and (old['data'].get('revision',1)!=value.revision or old['facility']!=value.facility_id):fail('비용이 변경되었거나 시설이 다릅니다. 다시 조회하세요.',409)
    d.update(intake.bind(conn,org,value.facility_id,value.receipt_id,old['data'] if old else None,d['evidence']))
    if old and (old['data'].get('receipt_id') or value.receipt_id) and not value.change_reason:fail('비용 변경 사유를 입력하세요.')
    d.update(revision=old['data'].get('revision',1)+1 if old else 1,settlement=True,amount=value.gross or '0',evidence_id=value.evidence[0].file if value.evidence else None)
    values=dict(org=org,facility=value.facility_id,data=d)
    if old:conn.execute(s.expenses.update().where(s.expenses.c.id==key).values(**values))
    else:key=conn.execute(s.expenses.insert().values(**values)).inserted_primary_key[0]
    store.log(conn,org,user,'settlement_expense',key,value.change_reason or '비용 사건·지급 증빙 저장',old,dict(values,id=key))
    return dict(id=key,revision=d['revision'],issues=expense_issues(d),affected_jobs=intake.affected(conn,org,'cost',key))

def job_save(conn,org,value,user,key=None):
    data=value.model_dump(exclude={'revision','request_key'});data['start'],data['end']=period(data['start'],data['end'])
    s.scoped(conn,s.facilities,org,value.facility_id)
    old=s.scoped(conn,jobs,org,key) if key else None
    if old:
        from epms.monthly_report import ensure_unlocked
        ensure_unlocked(conn,org,key)
    if not old:
        existing=conn.execute(jobs.select().where(jobs.c.org==org,jobs.c.create_key==value.request_key)).mappings().first()
        if existing:
            if existing['data']!=data:fail('같은 저장 요청의 내용이 다릅니다.',409)
            return dict(existing)
    if old and old['revision']!=value.revision:fail('다른 창에서 작업이 변경되었습니다. 다시 조회하세요.',409)
    if old and old['data']['facility_id']!=value.facility_id:fail('기존 정산 작업의 시설을 변경할 수 없습니다.')
    for edge in value.lines:
        if s.scoped(conn,s.batches,org,edge.batch_id)['facility']!=value.facility_id:fail('다른 시설의 선별기록을 혼합할 수 없습니다.')
        s.scoped(conn,store.declarations,org,edge.declaration_id)
        number(edge.left);number(edge.right)
    for cost in value.costs:
        if s.scoped(conn,s.expenses,org,cost.expense_id)['facility']!=value.facility_id:fail('다른 시설의 비용을 혼합할 수 없습니다.')
    revision=old['revision']+1 if old else 1
    row=dict(org=org,revision=revision,state='DRAFT',create_key=old['create_key'] if old else value.request_key,data=data)
    if old:conn.execute(jobs.update().where(jobs.c.id==key).values(**row))
    else:key=conn.execute(jobs.insert().values(**row)).inserted_primary_key[0]
    store.log(conn,org,user,'settlement_job',key,'정산 초안 저장',old,dict(row,id=key))
    return dict(row,id=key)

def _calculate(conn,org,data,key=None,strict=False):
    from epms.sorting_production import batch_committed
    from epms import sorting_receipts as intake
    from epms import support_budget as budget
    config=budget.effective(conn,org,data)
    managed=bool(config and config['data']['enabled'])
    if managed:
        data=dict(data,policy=dict(data['policy']))
        for field in ('unit_price','rate','rate_included','formula','rounding','tolerance_kg','tolerance_percent','tolerance_rule','reference'):
            data['policy'][field]=config['data'][field]
        data['policy']['label']=config['data']['title']
    policy=data['policy'];warnings=[];errors=[];details=[];cost_details=[]
    def error(message):errors.append(message)
    facility=s.scoped(conn,s.facilities,org,data['facility_id'])
    members=store.records(conn,store.memberships,org);index={rules.business_number(m['number']):m for m in members}
    contracts=contract_gate.context(conn,org)
    all_batches=s.list_rows(conn,s.batches,org)
    export_policies={r.year:r.data for r in conn.execute(store.policies.select().where(store.policies.c.org==org))}
    batch_sums={};decl_sums={};group_sums={};selected_batches={};seen=set();left_total=right_total=eligible=Decimal(0)
    for item in data['lines']:
        pair=(item['batch_id'],item['declaration_id'])
        if pair in seen:error('같은 선별기록·품목란 연결은 한 선에서 합쳐 입력하세요.')
        seen.add(pair)
        batch=s.scoped(conn,s.batches,org,item['batch_id']);source=batch['data']
        for issue in source_issues(source):error('선별기록: '+issue)
        if source.get('quantity_unknown') or batch['quantity']<=0:error('수출용 선별물량이 미확인이거나 0입니다.')
        if intake.blocked(conn,org,source):error('선별기록의 접수 묶음이 보완 필요 상태입니다.')
        if batch['facility']!=data['facility_id']:error('다른 시설의 선별기록입니다.')
        if batch['revision']!=item['batch_revision']:error('선별기록 원본이 변경되었습니다. 해당 연결을 새로 선택하세요.')
        try:declaration,logical,total=s.physical(conn,org,item['declaration_id'],item['declaration_revision'])
        except s.SortingError as e:error(str(e));continue
        original=declaration['data'];a,b=scaled(item['left']),scaled(item['right'])
        if min(a,b)<=0:error('연결 양쪽 물량은 0보다 커야 합니다.')
        producer=source.get('producer')
        if not source.get('confirmed') or not producer:error('선별기록의 생산자와 원문 확인을 완료하세요.')
        elif producer['id'] not in {m['id'] for m in members}:error('선별기록의 생산자가 명부에서 삭제되었습니다.')
        elif rules.business_number(producer['number'])!=rules.business_number(original.get('maker')):error('선별기록 생산자와 신고 제조자가 다릅니다.')
        export_day=rules.day(original.get('shipped')) or rules.day(original.get('declared'))
        export_policy=export_policies.get(export_day.year if export_day else data['year'])
        if not export_policy:warnings.append('해당 수출연도 정책이 미등록되어 기본 신고 검증만 적용한 참고 계산입니다.')
        issues,_=rules.validate_line(dict(original,id=declaration['id']),index,export_policy)
        for p in issues:
            if p['severity']!='INFO':error(p['message'])
        contract_issues,contract_evidence=contract_gate.evaluate(original,index,contracts)
        for p in contract_issues:error(p['message'])
        if source.get('total') is not None and number(source.get('total',''),True) is not None and Decimal(source['quantity'])>number(source['total']):error('수출물량이 총 투입물량보다 큽니다. 원본 입력을 수정하세요.')
        if source.get('warnings') and not source.get('overlap_reason'):error('선별물량 합계 차이에 대한 확인 사유를 입력하세요.')
        start=source.get('start',source.get('work_date',''));end=source.get('end',start)
        ship=rules.day(original.get('shipped'))
        if not ship:error('매칭할 수출신고필증의 선적일을 입력하세요.')
        elif not rules.day(start) or ship.isoformat()<start:error('선적일은 선별일보다 이전일 수 없습니다.')
        if not (facility['data']['valid_from']<=start<=end and (not facility['data'].get('valid_to') or end<=facility['data']['valid_to'])):error('시설 지정 유효기간과 선별기간을 확인하세요.')
        if start<data['start'] or end>data['end']:
            if not item['period_reason']:error('대상 기간·사업연도 밖 연결은 귀속 확인 사유가 필요합니다.')
            else:warnings.append('기간·연도 차이를 사용자 사유로 확인한 참고 계산입니다.')
        diff=abs(a-b);tolerance=[]
        if policy['tolerance_kg']!='':tolerance.append(diff<=scaled(policy['tolerance_kg']))
        if policy['tolerance_percent']!='':tolerance.append(Decimal(diff)*100<=Decimal(b)*number(policy['tolerance_percent']))
        within=(all(tolerance) if policy['tolerance_rule']=='둘 다' else any(tolerance)) if tolerance else diff==0
        evidence=refs(conn,org,item.get('evidence',[]))
        if diff:
            if not item['reason']:error('양쪽 중량 차이의 사유를 입력하세요.')
            if not within and (not item['exception_reason'] or not evidence):error('허용 기준 밖 차이는 추가 확인 사유와 근거 PDF 페이지가 필요합니다.')
            warnings.append(f"{source.get('title',source.get('external_id'))}: 양쪽 중량 차이 {s.text_quantity(a-b)} kg")
        batch_sums[batch['id']]=batch_sums.get(batch['id'],0)+a
        root_id,group_left=group_remaining(conn,org,batch['id'],key)
        group_sums[root_id]=group_sums.get(root_id,0)+a
        if group_sums[root_id]>group_left:error('원집계와 분할 기록을 합친 실제 수출물량을 초과했습니다. 일별·월별·생산자 분할 중복을 확인하세요.')
        decl_sums[logical]=decl_sums.get(logical,0)+b
        available_left=batch['quantity']-batch_committed(conn,org,batch['id'])+used_batch(conn,batch['id'])-used_batch(conn,batch['id'],key)
        legacy=sum(conn.execute(select(s.allocations.c.quantity).where(s.allocations.c.logical_key==logical,s.allocations.c.active==1)).scalars())
        available_right=total-legacy-used_declaration(conn,logical,key)
        if batch_sums[batch['id']]>available_left:error('선별기록의 실제 잔량을 초과했습니다. 다른 작업의 사용량도 확인하세요.')
        if decl_sums[logical]>available_right:error('신고 순중량의 실제 잔량을 초과했습니다. 다른 작업의 사용량도 확인하세요.')
        selected_batches[batch['id']]=batch
        aq,bq=Decimal(a)/1_000_000,Decimal(b)/1_000_000
        eq=min(aq,bq);eligible+=eq;left_total+=aq;right_total+=bq
        details.append(dict(item,batch_title=source.get('title',source.get('external_id')),producer=producer,
            batch_revision=batch['revision'],batch_data=source,declaration_data=original,logical_key=logical,eligible=str(eq),
            left_remaining=s.text_quantity(available_left-batch_sums[batch['id']]),right_remaining=s.text_quantity(available_right-decl_sums[logical]),
            evidence_refs=evidence,source_evidence=refs(conn,org,source.get('evidence',[])),contract_evidence=contract_evidence,available_left=available_left,available_right=available_right))
    # Overlapping daily/monthly records require an explicit separation explanation, including other jobs.
    for batch in selected_batches.values():
        d=batch['data'];producer=d.get('producer')
        if not producer:continue
        for other in all_batches:
            od=other['data']
            if other['id']==batch['id'] or other['facility']!=batch['facility'] or not od.get('producer') or od['producer']['id']!=producer['id']:continue
            active=other['id'] in selected_batches or used_batch(conn,other['id'],key)>0
            if active and d.get('start','')<=od.get('end',od.get('work_date','')) and od.get('start',od.get('work_date',''))<=d.get('end','') and not d.get('overlap_reason'):
                error('기간이 겹치는 일별·월별 기록이 있습니다. 별개 물량임을 원문과 확인하고 구분 사유를 기록하세요.')
    allocated=Decimal(0);cost_ids=set()
    for item in data['costs']:
        if item['expense_id'] in cost_ids:error('같은 비용 사건을 정산 작업에서 중복 배부할 수 없습니다.')
        cost_ids.add(item['expense_id'])
        expense=s.scoped(conn,s.expenses,org,item['expense_id']);d=expense['data']
        if expense['facility']!=data['facility_id'] or d.get('revision',1)!=item['revision']:error('비용의 시설 또는 수정 차수가 다릅니다.')
        if not d.get('settlement'):error('종전 비용은 지급 증빙을 확인하여 정산 비용 사건으로 보완하세요.');continue
        if not d.get('confirmed'):error('비용 지급 원문을 확인하세요.')
        for issue in expense_issues(d):error('비용: '+issue)
        if intake.blocked(conn,org,d):error('비용의 접수 묶음이 보완 필요 상태입니다.')
        if d['start']<data['start'] or d['end']>data['end']:warnings.append('비용 기간이 정산 기간과 다릅니다. 저장한 배부 근거를 함께 확인하세요.')
        if not d.get('batch_ids') or not set(d['batch_ids']) & set(selected_batches):error('비용의 관련 선별기록과 이번 연결 기록을 대조하세요.')
        net=max(Decimal(0),paid(d)-number(d['excluded']))
        if item['method'] in ('작업시간 비율','물량 비율'):
            numerator,denominator=number(item['numerator']),number(item['denominator'])
            if denominator<=0 or numerator>denominator:fail('비용 배부 비율의 분모·분자를 확인하세요.')
            amount=s.exact_product(net,numerator)/denominator
            amount=amount.quantize(Decimal('.000001'),rounding=ROUND_DOWN)
        else:amount=number(item['amount'])
        if amount>net-used_cost(conn,expense['id'],key):error('비용 원액의 순지급 잔액보다 초과 배부할 수 없습니다.')
        allocated+=amount
        cost_details.append(dict(item,amount=str(amount),title=d['title'],data=d,evidence_refs=refs(conn,org,d['evidence'])))
    missing=[];unit=number(policy['unit_price'],True);rate=number(policy['rate'],True)
    if unit is None:missing.append('kg당 단가')
    if not policy['rate_included'] and rate is None:missing.append('보조율 (%)')
    if rate is not None and rate>100:fail('보조율은 0~100%입니다.')
    if not data['lines']:error('선별기록과 수출신고 품목란을 연결하세요.')
    if policy['formula']=='실비 한도' and not data['costs']:missing.append('지급 확인 비용 배부')
    if not policy['assumptions_confirmed']:missing.append('가정값·참고 계산 확인')
    quantity_cap=number(policy['cap_quantity'],True)
    billable=min(eligible,quantity_cap) if quantity_cap is not None else eligible
    amount=None;formula='계산 기준 미입력';deduction=number(policy['deduction']);scope_used=Decimal(0);scope_available=None
    if not missing:
        limit=s.exact_product(billable,unit)
        base=min(limit,allocated) if policy['formula']=='실비 한도' else limit
        amount=base if policy['rate_included'] else s.exact_product(base,rate)/100
        if policy['cap']!='':amount=min(amount,number(policy['cap']))
        amount=max(Decimal(0),amount-deduction)
        amount=amount.quantize(Decimal('1'),rounding=ROUND_DOWN if policy['rounding']=='원 미만 절사' else ROUND_HALF_UP)
        formula=f"{billable} kg × {unit} 원/kg"+(' / 단가에 보조율 포함' if policy['rate_included'] else f' × {rate}%')
        if policy['formula']=='실비 한도':formula+=f' (배부 실비 {allocated}원 한도 비교 후 보조율 적용)'
        formula+=f" / 작업 한도 {policy['cap'] or '미설정'}원 / 차감 {deduction}원 / {policy['rounding']}"
        if policy['scope_cap']!='':
            other_total=Decimal(0)
            for j in s.list_rows(conn,jobs,org):
                if j['id']==key or j['data']['facility_id']!=data['facility_id'] or j['data']['year']!=data['year']:continue
                snap=conn.execute(snapshots.select().where(snapshots.c.job==j['id']).order_by(snapshots.c.id.desc())).mappings().first()
                if snap and (used_batch_for_job(conn,j['id'])):other_total+=number(snap['data']['result']['amount'])
            scope_used=other_total;scope_available=max(Decimal(0),number(policy['scope_cap'])-other_total)
            amount=min(amount,scope_available)
            amount=amount.quantize(Decimal('1'),rounding=ROUND_DOWN)
    for detail in details:
        detail['left_remaining']=s.text_quantity(detail.pop('available_left')-batch_sums[detail['batch_id']])
        detail['right_remaining']=s.text_quantity(detail.pop('available_right')-decl_sums[detail['logical_key']])
    result=dict(calculation_version='settlement-reference-1',label='가정값에 따른 참고 계산 / 외부 승인·지급 미확정',amount=None if amount is None else str(amount),
        eligible=str(eligible),billable=str(billable),left=str(left_total),right=str(right_total),difference=str(left_total-right_total),
        allocated_cost=str(allocated),scope_used=str(scope_used),scope_available=None if scope_available is None else str(scope_available),formula=formula,errors=list(dict.fromkeys(errors)),warnings=list(dict.fromkeys(warnings)),missing=missing,
        lines=details,costs=cost_details,policy=policy,quantity_basis='연결별 양쪽 확인물량 중 작은 값의 합계',
        tolerance_basis='각 연결의 신고 측 배분량을 분모로 하는 절대 차이율; 원본은 변경하지 않음')
    if managed:budget.apply(conn,org,data,result,key,config)
    elif key:
        previous=conn.execute(budget.usage.select().where(budget.usage.c.org==org,budget.usage.c.job==key,budget.usage.c.active==1)).mappings().first()
        if previous and previous['data']['kind']!='제외' and any(budget.won(a['amount'])>0 for a in previous['data']['members']):result['errors'].append('예산을 사용한 정산입니다. 재확정할 연도의 지원기준을 적용하거나 기존 확정을 먼저 해제하세요.')
    if strict and (result['errors'] or result['missing']):fail(' / '.join(result['errors']+result['missing']))
    return result

def calculate(conn,org,data,key=None,strict=False):
    with localcontext() as context:
        context.prec=60
        return _calculate(conn,org,data,key,strict)

def used_batch_for_job(conn,key):
    return conn.execute(select(links.c.id).where(links.c.job==key,links.c.active==1).limit(1)).first() is not None

def confirm(conn,org,key,value,user):
    fingerprint=s.digest(value.model_dump())
    existing=conn.execute(snapshots.select().where(snapshots.c.job==key,snapshots.c.request_key==value.request_key)).mappings().first()
    if existing:
        s.scoped(conn,jobs,org,key)
        if existing['input_hash']!=fingerprint:fail('같은 확정 요청키의 내용이 다릅니다.',409)
        return dict(id=existing['id'],revision=existing['revision'],snapshot=existing['data'])
    job=s.scoped(conn,jobs,org,key)
    from epms.monthly_report import ensure_unlocked
    ensure_unlocked(conn,org,key)
    if job['revision']!=value.revision:fail('작업이 변경되었습니다. 다시 조회하세요.',409)
    if value.facility_id!=job['data']['facility_id']:fail('정산 작업의 시설을 바꿀 수 없습니다.')
    data=value.model_dump(exclude={'revision','request_key'});data['start'],data['end']=period(data['start'],data['end'])
    result=calculate(conn,org,data,key,True)
    data['policy']=result['policy']
    conn.execute(links.update().where(links.c.job==key,links.c.active==1).values(active=0))
    conn.execute(cost_links.update().where(cost_links.c.job==key,cost_links.c.active==1).values(active=0))
    for row in result['lines']:
        conn.execute(links.insert().values(org=org,job=key,batch=row['batch_id'],declaration=row['declaration_id'],
            logical_key=row['logical_key'],left=scaled(row['left']),right=scaled(row['right']),active=1,data=row))
    for row in result['costs']:
        conn.execute(cost_links.insert().values(org=org,job=key,expense=row['expense_id'],amount=row['amount'],active=1))
    revision=job['revision']+1
    confirmation_number=conn.execute(select(func.count()).select_from(snapshots).where(snapshots.c.job==key)).scalar_one()+1
    snapshot=dict(job_id=key,revision=revision,confirmation_number=confirmation_number,time=store.now(),actor=user,organization=conn.execute(select(store.organizations.c.name).where(store.organizations.c.id==org)).scalar_one(),
        facility=s.scoped(conn,s.facilities,org,data['facility_id'])['data'],data=data,result=result)
    # Generate before committing so a failed PDF cannot leave partially confirmed balances.
    from epms.settlement_pdf import render
    pdf=render(snapshot)
    sid=conn.execute(snapshots.insert().values(org=org,job=key,revision=revision,request_key=value.request_key,input_hash=fingerprint,data=snapshot,pdf=pdf)).inserted_primary_key[0]
    from epms import support_budget as budget
    if result.get('support'):
        budget.write_usage(conn,org,key,data['year'],dict(kind='정산 확정',snapshot_id=sid,members=[dict(member_id=a['member_id'],name=a['name'],number=a['number'],amount=a['amount']) for a in result['support']['members']],support_revision=result['support']['revision'],time=store.now()),user)
    else:conn.execute(budget.usage.update().where(budget.usage.c.org==org,budget.usage.c.job==key).values(active=0))
    conn.execute(jobs.update().where(jobs.c.id==key).values(revision=revision,state='CONFIRMED',data=data))
    store.log(conn,org,user,'settlement_confirm',key,'연결 확정·참고 계산',None,dict(snapshot_id=sid,revision=revision,amount=result['amount']))
    return dict(id=sid,revision=revision,snapshot=snapshot)
