"""Local allocation-to-contract version links, without legal equivalence or export approval."""
from epms import sorting as s,store
KIND='LINE_CONTRACT_REVIEW'


def events(conn,org,line_id):
    return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==KIND).order_by(s.records.c.id)).mappings() if r['data']['line_id']==line_id]


def assess(conn,org,claim_id,line_id):
    s.scoped(conn,s.claims,org,claim_id);line=s.scoped(conn,s.allocations,org,line_id)
    if line['claim']!=claim_id:
        s.reject('SORT_E09','line','해당 신청의 배분행이 아닙니다.',404)
    history=events(conn,org,line_id)
    if not history or history[-1]['data']['contract_id'] is None:
        return dict(status='UNLINKED',contract_id=None,issues=['계약 연결 미확인'],history=history,approved=False)
    value=history[-1]['data'];contract=s.scoped(conn,s.records,org,value['contract_id']);issues=[]
    if s.digest(contract['data'])!=value['contract_hash'] or contract['revision']!=value['contract_revision']:
        issues.append('연결 계약 원본이 변경되었습니다.')
    if any(r['data'].get('previous_id')==contract['id'] for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='CONTRACT')).mappings()):
        issues.append('연결 계약의 새 개정본이 있습니다. 기존 연결을 자동 교체하지 않습니다.')
    batch=s.scoped(conn,s.batches,org,line['batch']);day=s.rules.day(batch['data'].get('work_date'))
    if day and not contract['data']['start']<=day.isoformat()<=contract['data']['end']:
        issues.append('선별일이 연결 계약기간 밖입니다. 적용 기준일 정책 검토가 필요합니다.')
    return dict(status='REVIEW_REQUIRED' if issues else 'RECORDED_FOR_REVIEW',contract_id=contract['id'],contract_revision=contract['revision'],issues=issues,
        history=history,approved=False,identity_assurance='LOCAL_UNVERIFIED')


def record(conn,org,claim_id,line_id,data,actor):
    claim=s.scoped(conn,s.claims,org,claim_id);line=s.scoped(conn,s.allocations,org,line_id)
    fingerprint=s.digest(dict(data,claim_id=claim_id,line_id=line_id));history=events(conn,org,line_id)
    for row in history:
        if row['data']['request_key']==data['request_key']:
            if row['data']['input_hash']!=fingerprint:
                s.reject('SORT_E08','request_key','같은 계약 연결 요청키의 내용이 다릅니다.')
            return dict(id=row['id'],revision=row['data']['result_revision'])
    if line['claim']!=claim_id or not line['active'] or claim['state'] in {'WITHDRAWN','REJECTED'} or claim['revision']!=data['revision']:
        s.reject('SORT_E08','revision','신청/행 상태·개정이 변경되었습니다.')
    if not data['reason'].strip():
        s.reject('SORT_E08','reason','계약 연결 사유가 필요합니다.',422)
    contract_id=data['contract_id'];contract=None
    if contract_id is not None:
        contract=s.scoped(conn,s.records,org,contract_id);batch=s.scoped(conn,s.batches,org,line['batch'])
        if contract['kind']!='CONTRACT' or contract['data']['facility_id']!=batch['facility'] or contract['data']['product']!=batch['data']['product']:
            s.reject('SORT_E09','contract','같은 시설·품목의 계약을 선택하세요.',422)
    value=dict(data,claim_id=claim_id,line_id=line_id,contract_hash=None if contract is None else s.digest(contract['data']),contract_revision=None if contract is None else contract['revision'],
        previous_id=history[-1]['id'] if history else None,input_hash=fingerprint,result_revision=claim['revision']+1,actor=actor,time=store.now(),approved=False)
    entity=conn.execute(s.records.insert().values(org=org,kind=KIND,revision=len(history)+1,data=value)).inserted_primary_key[0]
    conn.execute(s.claims.update().where(s.claims.c.id==claim_id).values(revision=value['result_revision']))
    store.log(conn,org,actor,'sorting_contract_link',entity,data['reason'],None,dict(claim_id=claim_id,line_id=line_id,contract_id=contract_id))
    return dict(id=entity,revision=value['result_revision'])
