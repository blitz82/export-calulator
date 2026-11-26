"""Versioned annual support criteria, member allocations and atomic settlement usage."""
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP
from pydantic import Field
from sqlalchemy import Column,ForeignKey,Integer,JSON,String,UniqueConstraint,select
from fastapi import Request
from copy import deepcopy
from epms import store,sorting as s,settlement as st

plans=s.table('support_plans',Column('year',Integer,nullable=False),Column('revision',Integer,nullable=False),Column('data',JSON,nullable=False),UniqueConstraint('org','year'))
usage=s.table('support_usage',Column('year',Integer,nullable=False),Column('job',ForeignKey(st.jobs.c.id),nullable=False),Column('active',Integer,nullable=False),Column('data',JSON,nullable=False),UniqueConstraint('org','job'))
TABLES=(plans,usage)

class Allocation(st.Strict):
    member_id:int
    amount:str

class Period(st.Strict):
    key:str=Field(min_length=1,max_length=100)
    title:str=Field(min_length=1,max_length=100)
    start:str
    end:str
    unit_price:str
    rate_included:bool=True
    rate:str=''

class Plan(st.Strict):
    revision:int|None=None
    enabled:bool=False
    status:str=Field(default='가정값',pattern='^(가정값|적용 기준)$')
    title:str=Field(min_length=1,max_length=200)
    start:str
    end:str
    total:str=''
    unit_price:str=''
    rate_included:bool=True
    rate:str=''
    formula:str=Field(default='물량 단가',pattern='^(물량 단가|실비 한도)$')
    rounding:str=Field(default='원 미만 절사',pattern='^(원 미만 절사|원 단위 반올림)$')
    tolerance_kg:str=''
    tolerance_percent:str=''
    tolerance_rule:str=Field(default='둘 다',pattern='^(둘 다|하나 이상)$')
    reference:str=Field(default='',max_length=3000)
    documents:list[st.EvidenceRef]=Field(default_factory=list,max_length=30)
    allocations:list[Allocation]=Field(default_factory=list,max_length=10000)
    reason:str=Field(min_length=1,max_length=1000)
    date_basis:str=Field(default='선별기간',pattern='^(선별기간|선별시작일|선적일|신고일)$')
    periods:list[Period]=Field(default_factory=list,max_length=30)

class LegacyReview(st.Strict):
    snapshot_id:int
    decision:str=Field(pattern='^(반영|제외)$')
    allocations:list[Allocation]=Field(default_factory=list,max_length=10000)
    reason:str=Field(min_length=1,max_length=1000)

def won(value,optional=False):
    n=st.number(value,optional)
    if n is not None and n!=n.to_integral_value():st.fail('예산·배정액은 0 이상의 원 단위 정수로 입력하세요.')
    if n is not None and n>9_000_000_000_000_000:st.fail('예산·배정액의 입력 범위를 초과했습니다.')
    return n

def plan(conn,org,year):return conn.execute(plans.select().where(plans.c.org==org,plans.c.year==year)).mappings().first()

def effective(conn,org,data):
    row=plan(conn,org,data['year'])
    if not row or not row['data']['enabled'] or not row['data'].get('periods'):return row
    row=deepcopy(dict(row));d=row['data'];matched={};errors=[]
    for edge in data['lines']:
        batch=s.scoped(conn,s.batches,org,edge['batch_id'])['data'];decl=s.scoped(conn,store.declarations,org,edge['declaration_id'])['data'];basis=d.get('date_basis','선별기간')
        a=batch.get('start','') if basis in ('선별기간','선별시작일') else decl.get('shipped' if basis=='선적일' else 'declared','')
        b=batch.get('end','') if basis=='선별기간' else a
        found=[p for p in d['periods'] if a and b and p['start']<=a<=b<=p['end']]
        if len(found)!=1:errors.append(f'{basis} {a} ~ {b}: 적용 기간이 없거나 단가 변경일을 걸칩니다. 원문에 근거해 기간별 기록·정산으로 나누세요.')
        else:matched[found[0]['key']]=found[0]
    if len(matched)>1:errors.append('서로 다른 지원단가 기간이 한 작업에 있습니다. 기간별 정산으로 나누면 같은 보고월에 함께 제출할 수 있습니다.')
    if len(matched)==1:
        chosen=next(iter(matched.values()));d.update({k:chosen[k] for k in ('unit_price','rate_included','rate')});d['title']+=' · '+chosen['title'];d['effective_period']=chosen
    row['period_errors']=errors
    return row

def usage_rows(conn,org,year,exclude=None):
    query=usage.select().where(usage.c.org==org,usage.c.year==year,usage.c.active==1)
    if exclude is not None:query=query.where(usage.c.job!=exclude)
    return [dict(r,title=s.scoped(conn,st.jobs,org,r['job'])['data']['title']) for r in conn.execute(query).mappings()]

def totals(conn,org,year,exclude=None):
    used=Decimal(0);members={}
    for row in usage_rows(conn,org,year,exclude):
        if row['data']['kind']=='제외':continue
        for a in row['data']['members']:
            n=won(a['amount']);used+=n;members[a['member_id']]=members.get(a['member_id'],Decimal(0))+n
    return used,members

def legacy_pending(conn,org,year):
    reviewed={r['job']:r for r in usage_rows(conn,org,year)};pending=[]
    for job in s.list_rows(conn,st.jobs,org):
        if job['data']['year']!=year or not st.used_batch_for_job(conn,job['id']):continue
        snapshot=conn.execute(st.snapshots.select().where(st.snapshots.c.job==job['id']).order_by(st.snapshots.c.id.desc())).mappings().first()
        if not snapshot:continue
        record=reviewed.get(job['id'])
        if record and record['data'].get('snapshot_id')==snapshot['id']:continue
        pending.append(dict(job_id=job['id'],title=job['data']['title'],snapshot_id=snapshot['id'],amount=snapshot['data']['result']['amount'],time=snapshot['data']['time']))
    return pending

def overview(conn,org,year):
    row=plan(conn,org,year);used,member_used=totals(conn,org,year)
    if not row:return dict(plan=None,used=str(used),members=[],pending=legacy_pending(conn,org,year),usage=[])
    d=row['data'];assigned=sum((won(a['amount']) for a in d['allocations']),Decimal(0));total=won(d['total'],True)
    members=[]
    for a in d['allocations']:
        n=member_used.get(a['member_id'],Decimal(0));members.append(dict(a,used=str(n),remaining=str(won(a['amount'])-n)))
    return dict(plan=dict(row),used=str(used),assigned=str(assigned),unassigned=None if total is None else str(total-assigned),remaining=None if total is None else str(total-used),members=members,pending=legacy_pending(conn,org,year),usage=usage_rows(conn,org,year))

def save_plan(conn,org,year,value,user):
    old=plan(conn,org,year)
    if old and value.revision!=old['revision']:st.fail('지원기준이 변경되었습니다. 다시 조회하세요.',409)
    if not old and value.revision is not None:st.fail('새 지원기준을 다시 조회하세요.',409)
    start,end=st.period(value.start,value.end)
    if start[:4]!=str(year) or end[:4]!=str(year):st.fail('적용 기간은 선택한 사업연도 안으로 지정하세요.')
    total=won(value.total,True);unit=st.number(value.unit_price,True);rate=st.number(value.rate,True)
    if value.enabled and (total is None or unit is None or (not value.rate_included and rate is None)):st.fail('적용할 전체 예산·단가·보조율을 입력하세요.')
    if rate is not None and rate>100:st.fail('보조율은 0~100%입니다.')
    st.number(value.tolerance_kg,True);percent=st.number(value.tolerance_percent,True)
    if percent is not None and percent>100:st.fail('허용 차이율은 0~100%입니다.')
    st.refs(conn,org,value.documents)
    keys=set();previous_end=''
    for p in sorted(value.periods,key=lambda p:p.start):
        a,b=st.period(p.start,p.end)
        if a<start or b>end or p.key in keys or (previous_end and a<=previous_end):st.fail('기간별 지원기준의 날짜·중복·겹침을 확인하세요.')
        keys.add(p.key);previous_end=b;st.number(p.unit_price)
        if not p.rate_included and (p.rate=='' or st.number(p.rate)>100):st.fail('기간별 지원 보조율을 0~100%로 입력하세요.')
    members={m['id']:m for m in store.records(conn,store.memberships,org)};allocations=[];seen=set();assigned=Decimal(0)
    previous={a['member_id']:a for a in old['data']['allocations']} if old else {}
    for a in value.allocations:
        if a.member_id in seen:st.fail('같은 회원사에 배정액을 중복 입력할 수 없습니다.')
        seen.add(a.member_id);member=members.get(a.member_id)
        if not member and a.member_id not in previous:st.fail('회원사 명부에서 배정 대상을 선택하세요.')
        if member and member.get('kind') not in {'생산자','복수'} and a.member_id not in previous:st.fail('배정 대상은 생산자회원사입니다. 지급 대상·운영자와 구분하세요.')
        n=won(a.amount);assigned+=n
        allocations.append(dict(member_id=a.member_id,amount=str(n),name=member['name'] if member else previous[a.member_id]['name'],number=member['number'] if member else previous[a.member_id]['number']))
    if total is not None and assigned>total:st.fail('회원사 배정 합계가 전체 지원 예산을 초과합니다.')
    used,member_used=totals(conn,org,year)
    if used and not value.enabled:st.fail('정산 확정 사용액이 있어 지원기준을 해제할 수 없습니다. 해당 정산을 먼저 검토하세요.')
    if total is not None and total<used:st.fail('전체 예산을 이미 확정한 사용액보다 줄일 수 없습니다.')
    caps={a['member_id']:won(a['amount']) for a in allocations}
    for member,n in member_used.items():
        if caps.get(member,Decimal(0))<n:st.fail('회원사 배정액을 이미 확정한 사용액보다 줄이거나 제거할 수 없습니다.')
    d=value.model_dump(exclude={'revision'});d.update(allocations=allocations,start=start,end=end,beneficiary='생산자회원사')
    revision=old['revision']+1 if old else 1;values=dict(org=org,year=year,revision=revision,data=d)
    if old:conn.execute(plans.update().where(plans.c.id==old['id']).values(**values));key=old['id']
    else:key=conn.execute(plans.insert().values(**values)).inserted_primary_key[0]
    store.log(conn,org,user,'support_plan',key,value.reason,dict(old) if old else None,dict(values,id=key))
    return dict(values,id=key)

def distribute(total,weights):
    """Deterministic largest remainder allocation, no loss of integer won."""
    total=int(total);denominator=sum(weights.values(),Decimal(0))
    if not denominator:return {k:0 for k in weights}
    raw={k:Decimal(total)*v/denominator for k,v in weights.items()};result={k:int(v) for k,v in raw.items()}
    for key in sorted(weights,key=lambda k:(-(raw[k]-result[k]),k))[:total-sum(result.values())]:result[key]+=1
    return result

def apply(conn,org,data,result,key,config):
    d=config['data'];result['support']=dict(year=data['year'],revision=config['revision'],title=d['title'],status=d['status'],reference=d['reference'],documents=st.refs(conn,org,d['documents']),beneficiary='생산자회원사',members=[])
    details=result['support'];errors=result['errors'];missing=result['missing']
    errors.extend(config.get('period_errors',[]))
    details['date_basis']=d.get('date_basis','선별기간');details['effective_period']=d.get('effective_period')
    if data.get('support_revision')!=config['revision']:missing.append('최신 지원기준·배정액 확인')
    pending=legacy_pending(conn,org,data['year'])
    if pending:errors.append('기존 확정액의 예산 반영·제외 검토를 먼저 완료하세요.')
    weights={};parties={}
    for e in result['lines']:
        member=e.get('producer')
        if not member:continue
        mid=member['id'];parties[mid]=member;weights[mid]=weights.get(mid,Decimal(0))+st.number(e['eligible'])
        start=e['batch_data'].get('start','');end=e['batch_data'].get('end','')
        if not d.get('periods') and (start<d['start'] or end>d['end']):errors.append('선별기간이 적용 지원기준의 기간 밖입니다.')
    used,member_used=totals(conn,org,data['year'],key);budget=won(d['total']);remaining=max(Decimal(0),budget-used)
    details.update(total=str(budget),used=str(used),remaining=str(remaining),pending_count=len(pending))
    caps={a['member_id']:a for a in d['allocations']}
    member_costs={};cost_sum=Decimal(0)
    for item in data.get('member_costs',[]):
        mid=item['member_id']
        if mid in member_costs or mid not in weights:errors.append('실비 배부의 회원사 중복·대상을 확인하세요.');continue
        amount=won(item['amount']);member_costs[mid]=amount;cost_sum+=amount
    if d['formula']=='실비 한도' and (set(member_costs)!=set(weights) or cost_sum!=st.number(result['allocated_cost'])):errors.append('확인된 배부 실비를 생산자회원사별로 나누어 입력하세요. 합계가 배부 비용과 같아야 합니다.')
    total_weight=sum(weights.values(),Decimal(0));billable=st.number(result['billable']);raw={}
    for mid,q in weights.items():
        adjusted=q*billable/total_weight if total_weight else Decimal(0)
        base=st.s.exact_product(adjusted,st.number(d['unit_price']))
        if d['formula']=='실비 한도':base=min(base,member_costs.get(mid,Decimal(0)))
        raw[mid]=base if d['rate_included'] else base*st.number(d['rate'])/100
    gross=sum(raw.values(),Decimal(0));work_cap=st.number(data['policy']['cap'],True)
    target=min(gross,work_cap) if work_cap is not None else gross
    target=max(Decimal(0),target-st.number(data['policy']['deduction']))
    if data['policy']['scope_cap']!='' and result['scope_available'] is not None:target=min(target,st.number(result['scope_available']))
    target=target.quantize(Decimal(1),rounding=ROUND_DOWN if d['rounding']=='원 미만 절사' else ROUND_HALF_UP)
    bases=distribute(target,raw);applied=Decimal(0)
    for mid in sorted(weights):
        allocation=caps.get(mid);spent=member_used.get(mid,Decimal(0));available=max(Decimal(0),won(allocation['amount'])-spent) if allocation else Decimal(0)
        if not allocation:errors.append(parties[mid]['name']+': 회원사 배정액을 설정하세요.')
        base=Decimal(bases[mid]);amount=min(base,available);applied+=amount
        details['members'].append(dict(member_id=mid,name=parties[mid]['name'],number=parties[mid]['number'],matched_kg=str(weights[mid]),base_amount=str(base),allocation=None if not allocation else allocation['amount'],used=str(spent),remaining=str(available),amount=str(amount),unfunded=str(base-amount),after=str(available-amount),cost=str(member_costs.get(mid,Decimal(0))),status='배정 미설정' if not allocation else '회원사 한도 일부 적용' if amount<base else '지원액 전액 반영'))
    if applied>remaining:errors.append('전체 지원 예산 잔액이 부족합니다. 배정·사용 내역을 확인하세요.')
    own=conn.execute(usage.select().where(usage.c.org==org,usage.c.job==key,usage.c.active==1)).mappings().first() if key else None
    previous=sum((won(a['amount']) for a in own['data']['members']),Decimal(0)) if own and own['year']==data['year'] and own['data']['kind']!='제외' else Decimal(0)
    details.update(base_amount=str(target),amount=str(applied),unfunded=str(target-applied),after=str(remaining-applied),previous_amount=str(previous),delta=str(applied-previous))
    result['amount']=str(applied);result['calculation_version']='settlement-budget-1';result['label']=d['status']+' 지원기준에 따른 정산 / 외부 승인·지급 미확정'
    result['formula']+=' / 생산자회원사별 계산·배정 잔액 및 전체 예산 검사'
    if target>applied:result['warnings'].append('한도 미반영 금액은 물량 잔량이 아닙니다. 증액 후 같은 정산을 재검토하여 중복 연결 없이 차액을 계산하세요.')

def write_usage(conn,org,job,year,data,user):
    old=conn.execute(usage.select().where(usage.c.org==org,usage.c.job==job)).mappings().first()
    values=dict(org=org,year=year,job=job,active=1,data=data)
    if old:conn.execute(usage.update().where(usage.c.id==old['id']).values(**values))
    else:conn.execute(usage.insert().values(**values))
    store.log(conn,org,user,'support_usage',job,data.get('reason','정산 예산 사용액 반영'),dict(old) if old else None,values)

def review_legacy(conn,org,year,job,value,user):
    config=plan(conn,org,year)
    if not config:st.fail('먼저 사업연도 지원기준을 저장하세요.')
    target=s.scoped(conn,st.jobs,org,job)
    if target['data']['year']!=year or not st.used_batch_for_job(conn,job):st.fail('해당 연도의 활성 확정 정산이 아닙니다.')
    snapshot=conn.execute(st.snapshots.select().where(st.snapshots.c.job==job).order_by(st.snapshots.c.id.desc())).mappings().first()
    if not snapshot or snapshot['id']!=value.snapshot_id:st.fail('확정 결과가 변경되었습니다. 다시 조회하세요.',409)
    if not any(p['job_id']==job for p in legacy_pending(conn,org,year)):st.fail('이미 검토된 확정액입니다.',409)
    members={a['member_id']:a for a in config['data']['allocations']};_,spent=totals(conn,org,year,job);rows=[];seen=set()
    for a in value.allocations:
        if a.member_id in seen or a.member_id not in members:st.fail('회원사 배정표에서 반영 대상을 선택하세요.')
        seen.add(a.member_id);n=won(a.amount)
        if n+spent.get(a.member_id,Decimal(0))>won(members[a.member_id]['amount']):st.fail('회원사 배정 잔액보다 큽니다.')
        rows.append(dict(members[a.member_id],amount=str(n)))
    old_amount=won(snapshot['data']['result']['amount'])
    if value.decision=='반영' and sum((won(a['amount']) for a in rows),Decimal(0))!=old_amount:st.fail('회원사별 반영액 합계는 기존 확정액과 같아야 합니다.')
    if value.decision=='제외' and rows:st.fail('제외 결정에는 배정 반영액을 입력하지 않습니다.')
    used,_=totals(conn,org,year,job)
    if value.decision=='반영' and used+old_amount>won(config['data']['total']):st.fail('전체 예산 잔액을 초과합니다.')
    write_usage(conn,org,job,year,dict(kind=value.decision,snapshot_id=snapshot['id'],members=rows,original_amount=str(old_amount),reason=value.reason,time=store.now()),user)

def install(app,db,gate,actor):
    prefix='/api/org/{org}/support-budgets'
    @app.get(prefix+'/{year}')
    def get(org:int,year:int):
        with db.connect() as conn:gate(conn,org);return overview(conn,org,year)
    @app.put(prefix+'/{year}')
    def save(org:int,year:int,value:Plan,request:Request):
        if not 1900<=year<=9999:st.fail('사업연도를 확인하세요.')
        with db.begin() as conn:conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return save_plan(conn,org,year,value,actor(request))
    @app.post(prefix+'/{year}/legacy/{job}')
    def legacy(org:int,year:int,job:int,value:LegacyReview,request:Request):
        with db.begin() as conn:conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);review_legacy(conn,org,year,job,value,actor(request));return overview(conn,org,year)
    @app.get(prefix+'/{year}/history')
    def history(org:int,year:int):
        with db.connect() as conn:
            gate(conn,org);config=plan(conn,org,year)
            return [] if not config else [dict(r) for r in conn.execute(store.audit.select().where(store.audit.c.org==org,store.audit.c.entity=='support_plan',store.audit.c.target==config['id']).order_by(store.audit.c.id.desc())).mappings()]
