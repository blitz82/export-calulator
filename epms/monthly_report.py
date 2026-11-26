"""Monthly reporting is separate from calculation and actual payment authority."""
from copy import deepcopy
from hashlib import sha256
from decimal import Decimal
from datetime import date
import re
from sqlalchemy import Column, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint, select
from pydantic import Field
from epms import store, sorting as s, settlement as st, rules
from epms.support_budget import won

reports=s.table('monthly_reports',Column('revision',Integer,nullable=False),Column('state',String,nullable=False),
    Column('previous',ForeignKey('sorting_monthly_reports.id')),Column('data',JSON,nullable=False))
packages=s.table('monthly_packages',Column('report',ForeignKey(reports.c.id),nullable=False),Column('data',JSON,nullable=False),
    Column('pdf',LargeBinary,nullable=False),Column('archive',LargeBinary,nullable=False),Column('sha',String,nullable=False),UniqueConstraint('report'))
claims=s.table('monthly_claims',Column('package',ForeignKey(packages.c.id),nullable=False),Column('job',ForeignKey(st.jobs.c.id),nullable=False),
    Column('member',Integer,nullable=False),Column('active',Integer,nullable=False),Column('data',JSON,nullable=False))
transfers=s.table('monthly_transfers',Column('package',ForeignKey(packages.c.id),nullable=False),Column('token',String,nullable=False),
    Column('active',Integer,nullable=False),Column('data',JSON,nullable=False))
TABLES=(reports,packages,claims,transfers)

class Amount(st.Strict):
    member_id:int=Field(ge=1)
    amount:str=Field(max_length=40)
class Portion(Amount):
    kg:str=Field(max_length=40)
class Selection(st.Strict):
    snapshot_id:int=Field(ge=1)
    portions:list[Portion]=Field(default_factory=list,max_length=1000)
    attribution:list[Amount]=Field(default_factory=list,max_length=1000)
    reason:str=Field(default='',max_length=2000)
class Review(st.Strict):
    member_id:int=Field(ge=1)
    decision:str=Field(default='',max_length=40)
    date:str=''
    reviewer:str=Field(default='',max_length=100)
    note:str=Field(default='',max_length=4000)
    difference_reason:str=Field(default='',max_length=4000)
    unpaid_reason:str=Field(default='',max_length=2000)
    expected_date:str=''
    evidence:list[st.EvidenceRef]=Field(default_factory=list,max_length=100)
class Resolution(st.Strict):
    key:str=Field(min_length=1,max_length=100)
    number:str=Field(min_length=1,max_length=100)
    date:str
    total:str=Field(max_length=40)
    scope_amount:str=Field(max_length=40)
    allocations:list[Amount]=Field(default_factory=list,max_length=1000)
    evidence:list[st.EvidenceRef]=Field(default_factory=list,max_length=100)
class Payment(st.Strict):
    key:str=Field(min_length=1,max_length=100)
    reference:str=Field(min_length=1,max_length=100)
    date:str
    recipient:str=Field(min_length=1,max_length=200)
    recipient_reason:str=Field(default='',max_length=2000)
    refund:bool=False
    amount:str=Field(max_length=40)
    allocations:list[Amount]=Field(default_factory=list,max_length=1000)
    evidence:list[st.EvidenceRef]=Field(default_factory=list,max_length=100)
    import_source:int|None=None
    import_rows:list[int]=Field(default_factory=list,max_length=2000)
class DeclarationEvidence(st.EvidenceRef):
    declaration_id:int=Field(ge=1)
class Draft(st.Strict):
    revision:int|None=None
    previous:int|None=None
    title:str=Field(min_length=1,max_length=200)
    year:int=Field(ge=1900,le=9999)
    month:int=Field(ge=1,le=12)
    start:str
    end:str
    destination:str=Field(min_length=1,max_length=200)
    writer:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=5000)
    reason:str=Field(default='',max_length=2000)
    selections:list[Selection]=Field(default_factory=list,max_length=300)
    reviews:list[Review]=Field(default_factory=list,max_length=1000)
    resolutions:list[Resolution]=Field(default_factory=list,max_length=300)
    payments:list[Payment]=Field(default_factory=list,max_length=300)
    evidence:list[st.EvidenceRef]=Field(default_factory=list,max_length=100)
    declaration_evidence:list[DeclarationEvidence]=Field(default_factory=list,max_length=1000)
    reference_ack:bool=False
    personal_check:bool=False
    source_change_ack:bool=False
class Action(st.Strict):
    revision:int
    reason:str=Field(min_length=1,max_length=2000)
class Submitted(Action):
    date:str
    destination:str=Field(min_length=1,max_length=200)
    method:str=Field(min_length=1,max_length=100)
    receipt:str=Field(default='',max_length=200)
    evidence:list[st.EvidenceRef]=Field(default_factory=list,max_length=30)

def ensure_unlocked(conn,org,job):
    row=conn.execute(select(claims.c.id).where(claims.c.org==org,claims.c.job==job,claims.c.active==1)).first()
    if row:st.fail('월별 제출본에 포함된 정산입니다. 월별 정산·지급보고에서 잠금 해제 사유를 기록하고 정정본을 작성하세요.',409)

def family(conn,org,key):
    if not key:return set()
    seen=set()
    while key:
        if key in seen:st.fail('정정본 연결 관계를 확인하세요.')
        seen.add(key);key=s.scoped(conn,reports,org,key)['previous']
    return seen

def latest_snapshot(conn,job):
    return conn.execute(st.snapshots.select().where(st.snapshots.c.job==job).order_by(st.snapshots.c.id.desc())).mappings().first()

def member_bases(snapshot,attribution=()):
    r=snapshot['result'];found={}
    for line in r['lines']:
        p=line['producer'];m=found.setdefault(p['id'],dict(member_id=p['id'],name=p['name'],number=p['number'],kg='0',amount=None))
        m['kg']=str(Decimal(m['kg'])+st.number(line['eligible']))
    support=r.get('support')
    if support:
        for m in support['members']:
            if m['member_id'] in found:found[m['member_id']]['amount']=m['amount']
    elif len(found)==1:next(iter(found.values()))['amount']=r['amount']
    elif attribution:
        seen=set();total=Decimal(0)
        for a in attribution:
            if a['member_id'] in seen or a['member_id'] not in found:st.fail('기존 계산의 회원사별 귀속을 확인하세요.')
            seen.add(a['member_id']);found[a['member_id']]['amount']=str(won(a['amount']));total+=won(a['amount'])
        if seen!=set(found) or total!=won(r['amount']):st.fail('회원사 귀속액 합계는 기존 확정 계산액과 같아야 합니다.')
    return list(found.values())

def catalog(conn,org):
    rows=[]
    for j in s.list_rows(conn,st.jobs,org):
        snap=latest_snapshot(conn,j['id'])
        if not snap or j['state']!='CONFIRMED':continue
        used={}
        for c in conn.execute(claims.select().where(claims.c.org==org,claims.c.job==j['id'],claims.c.active==1)).mappings():
            m=used.setdefault(c['member'],dict(amount=Decimal(0),kg=Decimal(0)))
            for k in m:m[k]+=Decimal(c['data'][k])
        bases=member_bases(snap['data'])
        for m in bases:m['reported_amount']=str(used.get(m['member_id'],{}).get('amount',0));m['reported_kg']=str(used.get(m['member_id'],{}).get('kg',0))
        rows.append(dict(snapshot_id=snap['id'],job_id=j['id'],title=j['data']['title'],year=j['data']['year'],start=j['data']['start'],end=j['data']['end'],amount=snap['data']['result']['amount'],members=bases))
    files=[dict(id=r['id'],name=r['name'],data=r['data']) for r in conn.execute(s.evidence.select().where(s.evidence.c.org==org)).mappings()]
    return dict(settlements=rows,files=files)

def save(conn,org,value,user,key=None):
    old=s.scoped(conn,reports,org,key) if key else None
    if old and old['state']!='DRAFT':st.fail('보존된 제출본은 수정하지 않습니다. 정정본을 만드세요.',409)
    if old and old['revision']!=value.revision:st.fail('다른 창에서 보고서가 변경되었습니다.',409)
    data=value.model_dump(exclude={'revision','previous'});data['start'],data['end']=st.period(data['start'],data['end'])
    if not data['reason']:
        if value.previous:st.fail('정정본 작성 사유를 입력하세요.')
        data['reason']='월별 보고 초안 저장' if old else '월별 보고 최초 등록'
    if rules.day(data['start']).year!=value.year or rules.day(data['end']).year!=value.year:st.fail('보고 대상 기간은 사업연도 안에서 지정하세요.')
    previous=old['previous'] if old else value.previous
    if previous:
        parent=s.scoped(conn,reports,org,previous)
        if parent['state']=='DRAFT':st.fail('제출본을 보존한 보고서에서 정정본을 작성하세요.')
        if parent['data']['year']!=value.year or parent['data']['month']!=value.month:st.fail('정정본의 보고연월을 원본과 같게 지정하세요.')
        # One successor draft/final per version, so two windows cannot fork corrections.
        children=conn.execute(select(reports.c.id).where(reports.c.previous==previous)).scalars().all()
        if any(c!=key for c in children):st.fail('이미 작성 중이거나 보존된 정정본이 있습니다.',409)
    for selected in value.selections:s.scoped(conn,st.snapshots,org,selected.snapshot_id)
    for evidence in value.declaration_evidence:s.scoped(conn,store.declarations,org,evidence.declaration_id)
    from epms.workbench import imports
    for payment in value.payments:
        if payment.import_source:
            original=s.scoped(conn,imports,org,payment.import_source)
            numbers=original['data'].get('row_numbers',list(range(2,len(original['data']['rows'])+2)))
            if not payment.import_rows or not set(payment.import_rows)<=set(numbers):st.fail('지급 명세 원본 행을 확인하세요.')
    for refs in [value.evidence,value.declaration_evidence,*[v.evidence for v in value.reviews],*[v.evidence for v in value.resolutions],*[v.evidence for v in value.payments]]:st.refs(conn,org,refs)
    row=dict(org=org,revision=(old['revision']+1 if old else 1),state='DRAFT',previous=previous,data=data)
    if old:conn.execute(reports.update().where(reports.c.id==key).values(**row))
    else:key=conn.execute(reports.insert().values(**row)).inserted_primary_key[0]
    store.log(conn,org,user,'monthly_report',key,data['reason'],dict(old) if old else None,dict(row,id=key))
    return dict(row,id=key)

def preview(conn,org,key):
    report=s.scoped(conn,reports,org,key);d=report['data'];errors=[];warnings=[];members={};selected=[];documents={};exclude=family(conn,org,report['previous'])
    exclude_packages=set(conn.execute(select(packages.c.id).where(packages.c.org==org,packages.c.report.in_(exclude))).scalars()) if exclude else set()
    def refs(values,label,required=False):
        if required and not values:errors.append(label+': 근거 PDF를 연결하세요.')
        out=st.refs(conn,org,values)
        for ref in out:
            token='evidence:'+str(ref['file']);entry=documents.setdefault(token,dict(token=token,kind='evidence',file=ref['file'],name=ref['name'],sha=ref['sha'],uses=[]))
            use=dict(label=label,page=ref['page'])
            if use not in entry['uses']:entry['uses'].append(use)
        return out
    seen=set()
    for selection in d['selections']:
        snap=s.scoped(conn,st.snapshots,org,selection['snapshot_id']);job=s.scoped(conn,st.jobs,org,snap['job']);sd=snap['data'];r=sd['result']
        if snap['job'] in seen:errors.append('같은 정산 작업은 한 번만 선택하세요.');continue
        seen.add(snap['job'])
        current=latest_snapshot(conn,snap['job'])
        if job['state']!='CONFIRMED' or not current or current['id']!=snap['id']:errors.append(sd['data']['title']+': 최신 활성 확정 결과를 선택하세요.')
        if sd['data']['year']!=d['year']:errors.append('다른 사업연도의 정산은 포함할 수 없습니다.')
        if sd['data']['start']<d['start'] or sd['data']['end']>d['end']:
            if not selection['reason']:errors.append('대상 기간 밖 정산의 귀속 사유를 입력하세요.')
        config=r.get('support');reference=not config or config.get('status')!='적용 기준'
        if reference:
            warnings.append(sd['data']['title']+': 가정값·참고 계산을 포함합니다.')
            if not d['reference_ack']:errors.append('참고 계산을 포함하는 보고서임을 확인하세요.')
        if config:
            from epms.support_budget import plan
            now=plan(conn,org,sd['data']['year'])
            if now and now['revision']!=config['revision']:warnings.append('현재 지원기준과 확정 당시 기준의 개정 버전이 다릅니다. 확정 당시 기준을 사용합니다.')
            refs(config.get('documents',[]),'지원기준')
        bases=member_bases(sd,selection.get('attribution',[]));by_id={m['member_id']:m for m in bases}
        if any(m['amount'] is None for m in bases):errors.append('여러 회원사가 포함된 기존 계산은 회원사별 귀속액을 입력하고 합계를 확인하세요.');continue
        portions=selection['portions'] or [dict(member_id=m['member_id'],amount=m['amount'],kg=m['kg']) for m in bases]
        used_job=sum((won(c['data']['amount']) for c in conn.execute(claims.select().where(claims.c.org==org,claims.c.job==snap['job'],claims.c.active==1)).mappings() if c['package'] not in exclude_packages),Decimal(0))
        if used_job+sum((won(p['amount']) for p in portions),Decimal(0))>won(r['amount']):errors.append('동일 정산의 보고 금액이 확정 총액을 초과합니다.')
        pseen=set();items=[]
        for p in portions:
            mid=p['member_id'];base=by_id.get(mid)
            if not base or mid in pseen:errors.append('보고 대상 회원사 배분을 확인하세요.');continue
            pseen.add(mid);amount=won(p['amount']);kg=st.number(p['kg'])
            if kg>st.number(base['kg']) or amount>won(base['amount']):errors.append(base['name']+': 확정 계산액·물량보다 큰 보고 배분입니다.')
            if (amount!=won(base['amount']) or kg!=st.number(base['kg'])) and not selection['reason']:errors.append('일부 금액·물량 보고의 배분 근거를 입력하세요.')
            used_amount=Decimal(0);used_kg=Decimal(0)
            for c in conn.execute(claims.select().where(claims.c.org==org,claims.c.job==snap['job'],claims.c.member==mid,claims.c.active==1)).mappings():
                if c['package'] not in exclude_packages:
                    used_amount+=won(c['data']['amount']);used_kg+=st.number(c['data']['kg'])
                    if c['data'].get('base_amount') is not None and won(c['data']['base_amount'])!=won(base['amount']):errors.append('같은 원 계산의 회원사별 귀속액이 다른 보고와 다릅니다. 정정본에서 일관되게 확인하세요.')
            if used_amount+amount>won(base['amount']) or used_kg+kg>st.number(base['kg']):errors.append(base['name']+': 다른 제출본과 계산액·물량이 중복됩니다.')
            # A corrected legacy attribution must agree with any still-active sibling report.
            if used_amount>won(base['amount']):errors.append('이전 보고의 귀속액과 회원사별 배분을 확인하세요.')
            m=members.setdefault(mid,dict(member_id=mid,name=base['name'],number=base['number'],amount='0',kg='0',decision=None,resolved='0',paid='0',refund='0',net='0'))
            m['amount']=str(won(m['amount'])+amount);m['kg']=str(st.number(m['kg'])+kg)
            items.append(dict(p,name=base['name'],number=base['number'],job=snap['job'],base_amount=base['amount'],base_kg=base['kg']))
        for line in r['lines']:
            refs(line['source_evidence'],'선별기록: '+line['batch_title']);refs(line['evidence_refs'],'연결 차이 근거')
            declaration=s.scoped(conn,store.declarations,org,next(e['declaration_id'] for e in sd['data']['lines'] if e['batch_id']==line['batch_id'] and e['declaration_id']==line['declaration_id']))
            if declaration['revision']!=line['declaration_revision']:warnings.append('확정 이후 수출신고 품목란이 변경되었습니다. 당시 입력값과 원문을 대조하세요.')
            source=s.scoped(conn,s.batches,org,line['batch_id'])
            if source['revision']!=line['batch_revision']:warnings.append('확정 이후 선별기록이 변경되었습니다. 당시 입력값과 원문을 대조하세요.')
            file=None
            if declaration.get('source'):
                original=conn.execute(select(store.import_files.c.id,store.import_files.c.name,store.import_files.c.kind,store.import_files.c.data,store.import_rows.c.row).join(store.import_rows,store.import_rows.c.file==store.import_files.c.id).where(store.import_rows.c.id==declaration['source'],store.import_files.c.org==org)).mappings().first()
                if original and original['kind']=='pdf':file=original
            manual=[v for v in d.get('declaration_evidence',[]) if v['declaration_id']==line['declaration_id']]
            if manual:refs([dict(file=v['file'],page=v['page']) for v in manual],'수출신고필증 '+line['declaration_data']['number'])
            elif file:
                token='import:'+str(file['id']);entry=documents.setdefault(token,dict(token=token,kind='import',file=file['id'],name=file['name'],sha=sha256(file['data']).hexdigest(),uses=[]));entry['uses'].append(dict(label='수출신고필증 '+line['declaration_data']['number'],page=file['row']))
            else:
                errors.append('수출신고필증 원본 PDF·페이지를 연결하세요: '+line['declaration_data']['number']+' / '+line['declaration_data']['line'])
        for cost in r['costs']:refs(cost['evidence_refs'],'선별 작업비용: '+cost['title'])
        selected.append(dict(snapshot_id=snap['id'],job=snap['job'],snapshot=sd,portions=items,reason=selection['reason']))
    if not selected:errors.append('확정 정산 결과를 선택하세요.')
    review_map={}
    for review in d['reviews']:
        mid=review['member_id']
        if mid not in members or mid in review_map:errors.append('내부검토 회원사가 중복되거나 보고 대상이 아닙니다.');continue
        review_map[mid]=review;m=members[mid];m['review']=review
        if review['decision']=='':errors.append(m['name']+': 내부결정액을 입력하세요.')
        else:m['decision']=str(won(review['decision']))
        if not rules.day(review['date']) or not review['reviewer']:errors.append(m['name']+': 내부검토일·검토자를 입력하세요.')
        refs(review['evidence'],m['name']+' 내부검토',True)
        if review['expected_date'] and not rules.day(review['expected_date']):errors.append('지급 예정일을 확인하세요.')
    for mid,m in members.items():
        if mid not in review_map:errors.append(m['name']+': 내부검토가 없습니다.')
    resolution_keys=set();payment_keys=set();transfer_claims=[]
    for kind,events in [('결의',d['resolutions']),('지급',d['payments'])]:
        keys=resolution_keys if kind=='결의' else payment_keys
        for event in events:
            identity=event['number'] if kind=='결의' else event['reference']
            if event['key'] in keys:errors.append(kind+' 내역이 중복되었습니다.')
            keys.add(event['key'])
            if not rules.day(event['date']):errors.append(kind+' 일자를 확인하세요.')
            refs(event['evidence'],kind+' '+identity,True)
            gross=won(event['total'] if kind=='결의' else event['amount']);limit=won(event['scope_amount']) if kind=='결의' else gross
            if limit>gross:errors.append('사업 대상 결의액은 문서 총액을 초과할 수 없습니다.')
            allocated=Decimal(0);eseen=set()
            for a in event['allocations']:
                mid=a['member_id'];amount=won(a['amount']);allocated+=amount
                if mid in eseen or mid not in members:errors.append(kind+' 배분 대상이 중복되거나 보고 대상이 아닙니다.');continue
                eseen.add(mid);m=members[mid]
                field='resolved' if kind=='결의' else 'refund' if event['refund'] else 'paid'
                m[field]=str(won(m[field])+amount)
                if kind=='지급' and event['recipient']!=m['name'] and not event['recipient_reason']:errors.append('대상 회원사와 수취인이 다른 지급은 사유를 입력하세요.')
            if kind=='결의' and allocated!=limit:errors.append('회원사별 결의 배분합계는 이번 사업 대상 결의액과 같아야 합니다.')
            if kind=='지급':
                if allocated>limit or not event['allocations']:errors.append('지급 배분은 실제 거래금액 이내에서 입력하세요.')
            if kind in ('결의','지급'):
                token=s.digest(dict(kind=kind,reference=re.sub(r'\s+','',identity).casefold(),date=event['date'],refund=event.get('refund',False)))
                spent=Decimal(0)
                for t in conn.execute(transfers.select().where(transfers.c.org==org,transfers.c.token==token,transfers.c.active==1)).mappings():
                    if t['package'] in exclude_packages:continue
                    if won(t['data']['gross'])!=gross:errors.append('동일 지급 거래의 원금액이 다른 보고와 다릅니다.')
                    spent+=won(t['data']['amount'])
                own=sum((won(t['data']['amount']) for t in transfer_claims if t['token']==token),Decimal(0))
                if spent+own+allocated>gross:errors.append('동일 지급 거래가 다른 제출본 또는 이번 내역에 중복 배분되었습니다.')
                transfer_claims.append(dict(token=token,data=dict(gross=str(gross),amount=str(allocated),event=event)))
    for m in members.values():
        review=m.get('review',{});m['net']=str(won(m['paid'])-won(m['refund']));decision=won(m['decision']) if m['decision'] is not None else Decimal(0)
        m['review_difference']=str(won(m['amount'])-decision);m['resolution_difference']=str(decision-won(m['resolved']));m['unpaid']=str(won(m['resolved'])-Decimal(m['net']))
        if (Decimal(m['review_difference']) or Decimal(m['resolution_difference'])) and not review.get('difference_reason'):errors.append(m['name']+': 계산·결정·결의 차이 사유를 입력하세요.')
        if Decimal(m['unpaid'])>0 and not review.get('unpaid_reason'):errors.append(m['name']+': 미지급 사유를 입력하세요.')
        if decision>won(m['amount']) or won(m['resolved'])>decision or Decimal(m['net'])>won(m['resolved']) or Decimal(m['net'])<0:errors.append(m['name']+': 계산 초과 결정·결의·지급 또는 초과 반환을 해결하세요. 사유만으로 적정 처리할 수 없습니다.')
        m['status']='반환 후 미지급' if won(m['refund']) and Decimal(m['unpaid'])>0 else '미지급' if Decimal(m['unpaid'])>0 else '지급 내역 없음' if not won(m['paid']) else '지급 확인'
    refs(d['evidence'],'보고서 추가 증빙')
    if any('확정 이후' in w or '개정 버전' in w for w in warnings) and not d.get('source_change_ack'):errors.append('확정 이후 원본·기준 변경 사실과 당시 계산 유지 여부를 확인하세요.')
    if not d['personal_check']:errors.append('제출 증빙의 개인정보 표시 범위를 확인하세요.')
    totals={k:str(sum((Decimal(m.get(k) or '0') for m in members.values()),Decimal(0))) for k in ['amount','kg','decision','resolved','paid','refund','net','unpaid']}
    from epms.workbench import imports
    statements=[]
    for source in sorted({p['import_source'] for p in d['payments'] if p.get('import_source')}):
        original=s.scoped(conn,imports,org,source);statements.append(dict(id=source,name=original['name'],sha=original['sha']))
    return dict(report_id=key,revision=report['revision'],data=d,previous=report['previous'],members=list(members.values()),selections=selected,documents=list(documents.values()),statement_sources=statements,errors=list(dict.fromkeys(errors)),warnings=list(dict.fromkeys(warnings)),totals=totals,transfer_claims=transfer_claims)

def freeze(conn,org,key,revision,user):
    row=s.scoped(conn,reports,org,key)
    existing=conn.execute(packages.select().where(packages.c.report==key)).mappings().first()
    if existing:
        if row['revision']!=revision:st.fail('보고서 버전이 변경되었습니다.',409)
        return dict(id=existing['id'],sha=existing['sha'])
    if row['revision']!=revision or row['state']!='DRAFT':st.fail('보고서 버전 또는 상태가 변경되었습니다.',409)
    result=preview(conn,org,key)
    if result['errors']:st.fail(' / '.join(result['errors']))
    result.update(organization=conn.execute(select(store.organizations.c.name).where(store.organizations.c.id==org)).scalar_one(),created=store.now(),actor=user)
    from epms.monthly_report_pdf import render
    try:pdf,archive,manifest=render(conn,org,result)
    except Exception as error:
        from fastapi import HTTPException
        if isinstance(error,HTTPException):raise
        import logging
        logging.getLogger(__name__).exception('월별 통합 PDF 생성 실패')
        st.fail('통합 PDF 생성에 실패했습니다. 원본 PDF와 보고 범위를 확인하고 다시 시도하세요. 제출본은 저장되지 않았습니다.')
    transfer_claims=result['transfer_claims']
    result['manifest']=manifest;result.pop('transfer_claims',None)
    sha=sha256(pdf).hexdigest()
    pid=conn.execute(packages.insert().values(org=org,report=key,data=result,pdf=pdf,archive=archive,sha=sha)).inserted_primary_key[0]
    old=family(conn,org,row['previous'])
    oldpackages=list(conn.execute(select(packages.c.id).where(packages.c.org==org,packages.c.report.in_(old))).scalars()) if old else []
    if oldpackages:
        conn.execute(claims.update().where(claims.c.package.in_(oldpackages)).values(active=0));conn.execute(transfers.update().where(transfers.c.package.in_(oldpackages)).values(active=0))
        conn.execute(reports.update().where(reports.c.id.in_(old)).values(state='SUPERSEDED'))
        for old_report in old:store.log(conn,org,user,'monthly_report',old_report,'정정본으로 대체',None,dict(reason=row['data']['reason'],correction=key))
    for selection in result['selections']:
        for p in selection['portions']:conn.execute(claims.insert().values(org=org,package=pid,job=selection['job'],member=p['member_id'],active=1,data=dict(amount=p['amount'],kg=p['kg'],base_amount=p['base_amount'],snapshot_id=selection['snapshot_id'])))
    for t in transfer_claims:
        conn.execute(transfers.insert().values(org=org,package=pid,token=t['token'],active=1,data=t['data']))
    conn.execute(reports.update().where(reports.c.id==key).values(state='FROZEN',revision=row['revision']+1))
    store.log(conn,org,user,'monthly_package',key,'제출본 보존',None,dict(package_id=pid,sha=sha,reason=row['data']['reason']))
    return dict(id=pid,sha=sha,revision=row['revision']+1)
