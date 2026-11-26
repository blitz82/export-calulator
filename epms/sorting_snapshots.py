"""Immutable local review baselines and change candidates, never approved settlements."""
import hashlib,json
from decimal import Decimal
from epms import sorting as s,store

KIND='REVIEW_SNAPSHOT'


def capture(conn,org,claim_id,include_beneficiaries=True):
    claim=s.scoped(conn,s.claims,org,claim_id)
    allocations=[r for r in s.list_rows(conn,s.allocations,org) if r['claim']==claim_id]
    batches=[s.scoped(conn,s.batches,org,i) for i in sorted({r['batch'] for r in allocations})]
    facilities=[s.scoped(conn,s.facilities,org,i) for i in sorted({r['facility'] for r in batches})]
    declarations=[s.scoped(conn,store.declarations,org,i) for i in sorted({r['declaration'] for r in allocations})]
    cost_allocations=[r for r in s.list_rows(conn,s.expense_allocations,org) if r['claim']==claim_id]
    expenses=[s.scoped(conn,s.expenses,org,i) for i in sorted({r['expense'] for r in cost_allocations})]
    facility_ids={r['id'] for r in facilities};expense_ids={r['id'] for r in expenses}
    kinds=['CONTRACT','PLAN','PRODUCER_SETTLEMENT','CERTIFICATE','SANCTION','EXTERNAL_LEDGER','LINE_EVIDENCE_REVIEW','LINE_CONTRACT_REVIEW','PAYMENT_HISTORY','POLICY_DRAFT']
    if include_beneficiaries:kinds.extend(['BENEFICIARY_REVIEW','PAYMENT_PREPARATION_REVIEW'])
    candidates=[dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind.in_(kinds)).order_by(s.records.c.id)).mappings()]
    contracts={r['id'] for r in candidates if r['kind']=='CONTRACT' and r['data'].get('facility_id') in facility_ids}
    records=[]
    for row in candidates:
        d=row['data'];kind=row['kind']
        include=(kind in {'CONTRACT','PLAN','CERTIFICATE'} and d.get('facility_id') in facility_ids
            or kind=='PRODUCER_SETTLEMENT' and d.get('contract_id') in contracts
            or kind in {'LINE_EVIDENCE_REVIEW','LINE_CONTRACT_REVIEW'} and d.get('claim_id')==claim_id
            or kind=='BENEFICIARY_REVIEW' and d.get('claim_id')==claim_id
            or kind=='PAYMENT_PREPARATION_REVIEW' and d.get('claim_id')==claim_id
            or kind=='PAYMENT_HISTORY' and d.get('expense_id') in expense_ids
            or kind=='POLICY_DRAFT' and d.get('year')==claim['data']['year'] and d.get('product')==claim['data']['product']
            or kind=='SANCTION' and d.get('product')==claim['data']['product']
            or kind=='EXTERNAL_LEDGER')
        if include:
            records.append(row)
    members={r['data'].get('member_number') for r in facilities}
    for record in records:
        if record['kind']=='BENEFICIARY_REVIEW':members.update(r['member_number'] for r in record['data']['rows'])
    for row in declarations:
        members.update(row['data'].get(k) for k in ('owner','agent','maker'))
    memberships=[dict(r) for r in conn.execute(store.memberships.select().where(store.memberships.c.org==org,store.memberships.c.number.in_([m for m in members if m]))).mappings()]
    for member in memberships:
        member['data']={k:v for k,v in member['data'].items() if k in {'name','kind','applied','approved','withdrawn'}}
    body=dict(version='review-snapshot-1',claim=claim,allocations=allocations,batches=batches,facilities=facilities,declarations=declarations,
        cost_allocations=cost_allocations,expenses=expenses,records=records,memberships=memberships,policies=s.POLICIES)
    evidence_ids=set()
    def scan(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.endswith('evidence_id') and type(item)==int:
                    evidence_ids.add(item)
                scan(item)
        elif isinstance(value,list):
            for item in value:
                scan(item)
    scan(body)
    body['evidence']=[]
    for entity in sorted(evidence_ids):
        row=s.scoped(conn,s.evidence,org,entity)
        body['evidence'].append(dict(id=entity,sha=row['sha'],actual_sha=hashlib.sha256(row['content']).hexdigest()))
    keys={r['logical_key'] for r in allocations};used={key:0 for key in keys}
    for row in conn.execute(s.allocations.select().where(s.allocations.c.active==1,s.allocations.c.logical_key.in_(list(keys)))).mappings():
        used[row['logical_key']]+=row['quantity']
    pools=[]
    for key in sorted(keys):
        source=next(r for r in allocations if r['logical_key']==key)
        declaration=next(r for r in declarations if r['id']==source['declaration'])
        cap=None;problem=None
        try:
            _,current_key,cap=s.physical(conn,org,declaration['id'],declaration['revision'])
            if current_key!=key:
                cap=None;problem='신고 논리키가 변경되어 이전 예약 상한을 자동 연결하지 않음'
        except s.SortingError as exc:
            problem=exc.message
        pools.append(dict(key=key,reserved=s.text_quantity(used[key]),current_capacity=None if cap is None else s.text_quantity(cap),
            excess=None if cap is None else s.text_quantity(max(0,used[key]-cap)),issue=problem))
    body['pools']=pools;body['review']=s.review(conn,org,claim_id)
    return body


def get(conn,org,entity):
    row=s.scoped(conn,s.records,org,entity)
    if row['kind']!=KIND or s.digest(row['data']['snapshot'])!=row['data']['seal']:
        s.reject('SORT_E08','snapshot','검토 보존본의 종류·해시를 확인하세요.')
    return row


def save(conn,org,claim_id,data,actor):
    if not data['reason'].strip():
        s.reject('SORT_E08','reason','검토본 보존 사유가 필요합니다.',422)
    for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==KIND)).mappings():
        if r['data']['request_key']==data['request_key']:
            if r['data']['input_hash']!=s.digest(dict(data,claim_id=claim_id)):
                s.reject('SORT_E08','request_key','같은 보존 요청키의 내용이 다릅니다.')
            return dict(id=r['id'],seal=r['data']['seal'])
    claim=s.scoped(conn,s.claims,org,claim_id)
    if claim['revision']!=data['revision']:
        s.reject('SORT_E08','revision','신청 개정이 변경되었습니다.')
    body=capture(conn,org,claim_id)
    if len(json.dumps(body,ensure_ascii=False).encode())>20_000_000:
        s.reject('SORT_E08','snapshot','검토 보존본이 20MB를 초과합니다.',413)
    value=dict(snapshot=body,seal=s.digest(body),claim_id=claim_id,request_key=data['request_key'],input_hash=s.digest(dict(data,claim_id=claim_id)),
        reason=data['reason'],actor=actor,time=store.now(),approval_status='LOCAL_REVIEW_ONLY',approved_amount=None,payable_amount=None)
    entity=conn.execute(s.records.insert().values(org=org,kind=KIND,revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_review_snapshot',entity,data['reason'],None,dict(claim_id=claim_id,seal=value['seal']))
    return dict(id=entity,seal=value['seal'])


def history(conn,org,claim_id):
    s.scoped(conn,s.claims,org,claim_id)
    return [dict(id=r['id'],time=r['data']['time'],reason=r['data']['reason'],seal=r['data']['seal'],revision=r['data']['snapshot']['claim']['revision'])
        for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==KIND).order_by(s.records.c.id.desc())).mappings() if r['data']['claim_id']==claim_id][:50]


def compare(conn,org,entity):
    old=get(conn,org,entity);before=old['data']['snapshot']
    try:
        current=capture(conn,org,old['data']['claim_id'])
    except s.SortingError as exc:
        return dict(status='CURRENT_UNAVAILABLE',changes=[],message=exc.message,current_hash=None,payable_amount=None,recovery_amount=None)
    changes=[dict(part=key,before_hash=s.digest(before.get(key)),current_hash=s.digest(current.get(key))) for key in sorted(set(before)|set(current)) if before.get(key)!=current.get(key)]
    a,b=before['review']['reference_cost'],current['review']['reference_cost']
    return dict(status='CHANGED' if changes else 'UNCHANGED',changes=changes,current_hash=s.digest(current),
        previous_reference_cost=a,current_reference_cost=b,reference_cost_delta=None if a is None or b is None else str(Decimal(b)-Decimal(a)),
        quantity_candidates=[p for p in current['pools'] if p['excess'] is not None and Decimal(p['excess'])>0],
        quantity_issues=[p for p in current['pools'] if p['issue']],
        label='검토본 변경 비교 / 자동 조정·환수 아님',payable_amount=None,recovery_amount=None)


def candidate(conn,org,entity,data,actor):
    if not data['reason'].strip():
        s.reject('SORT_E08','reason','변경 검토 사유가 필요합니다.',422)
    old=get(conn,org,entity);result=compare(conn,org,entity)
    if result['status']!='CHANGED' or result['current_hash']!=data['current_hash']:
        s.reject('SORT_E08','current_hash','변경 결과를 다시 조회하세요.')
    for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='REVIEW_CHANGE_CANDIDATE')).mappings():
        if r['data']['snapshot_id']==entity and r['data']['current_hash']==data['current_hash']:
            return dict(id=r['id'],recovery_executed=False)
    value=dict(snapshot_id=entity,claim_id=old['data']['claim_id'],current_hash=data['current_hash'],comparison=result,reason=data['reason'],actor=actor,time=store.now(),
        execution='REVIEW_CANDIDATE_ONLY',recovery_executed=False,recovery_amount=None)
    target=conn.execute(s.records.insert().values(org=org,kind='REVIEW_CHANGE_CANDIDATE',revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_change_candidate',target,data['reason'],None,dict(snapshot_id=entity,current_hash=data['current_hash']))
    return dict(id=target,recovery_executed=False)
