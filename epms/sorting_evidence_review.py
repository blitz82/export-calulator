"""Append-only local evidence attestations. Never grants operational authority."""
import hashlib
from sqlalchemy import select
from epms import sorting as s, store, rules

KIND='LINE_EVIDENCE_REVIEW'
PRIMARY={'CUSTOMS_FULFILLMENT','LOADING_CONFIRMATION','OTHER_OBJECTIVE'}
INPUT_FIELDS=('revision','request_key','action','actual_shipped','fulfillment_evidence_id','fulfillment_kind','certificate_id','reason','claim_id','line_id')


def history(conn,org):
    query=s.records.select().where(s.records.c.kind==KIND)
    if org is not None:
        query=query.where(s.records.c.org==org)
    return [dict(r) for r in conn.execute(query.order_by(s.records.c.id)).mappings()]


def latest(conn,org):
    return {r['data']['line_id']:r for r in history(conn,org)}


def evidence_snapshot(conn,org,evidence_id):
    evidence=s.scoped(conn,s.evidence,org,evidence_id)
    if hashlib.sha256(evidence['content']).hexdigest()!=evidence['sha']:
        s.reject('SORT_E08','evidence_id','증빙 바이트와 저장 해시가 다릅니다.')
    return dict(id=evidence_id,sha=evidence['sha'])


def certificate_assessment(conn,org,line,data,batch,facility):
    if not data.get('certificate_id'):
        return 'MISSING',[]
    certificate=conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.id==data['certificate_id'],s.records.c.kind=='CERTIFICATE')).mappings().first()
    if certificate is None:
        return 'NEEDS_SUPPLEMENT',[s.problem('SORT_E08','certificate','연결한 확인서 기록이 없어졌습니다.')]
    issues=[]
    value=certificate['data']
    if s.digest(value)!=data.get('certificate_hash'):
        issues.append(s.problem('SORT_E08','certificate','연결 당시 확인서 기록이 변경되었습니다.'))
    if any(r.data.get('previous_id')==certificate['id'] for r in conn.execute(select(s.records.c.data).where(s.records.c.org==org,s.records.c.kind=='CERTIFICATE'))):
        issues.append(s.problem('SORT_E08','certificate','확인서의 새 버전이 있어 다시 연결해야 합니다.'))
    work=rules.day(batch['data'].get('work_date'))
    start,end=rules.day(value.get('start')),rules.day(value.get('end'))
    if (value.get('facility_id')!=batch['facility'] or value.get('product')!=batch['data']['product']
            or rules.business_number(value.get('member_number'))!=rules.business_number(facility['data']['member_number'])
            or not work or not start or not end or not (start<=work<=end)):
        issues.append(s.problem('SORT_E08','certificate','확인서의 시설·품목·회원·작업기간이 신청행과 다릅니다.'))
    used=0
    for other in latest(conn,None).values():
        if other['data']['action']=='REVOKE' or other['data'].get('certificate_evidence_sha')!=data.get('certificate_evidence_sha'):
            continue
        allocation=s.scoped(conn,s.allocations,other['org'],other['data']['line_id'])
        if allocation['active']:
            used+=allocation['quantity']
    try:
        limits=[s.kg(r.data.get('quantity')) for r in conn.execute(select(s.records.c.data).where(s.records.c.kind=='CERTIFICATE'))
                if r.data.get('evidence_hash')==data.get('certificate_evidence_sha')]
        certified_quantity=min(limits) if limits else s.kg(value.get('quantity'))
    except s.SortingError:
        certified_quantity=None
        issues.append(s.problem('SORT_E08','certificate','확인서 물량 기록이 유효하지 않습니다.'))
    if certified_quantity is not None and used>certified_quantity:
        issues.append(s.problem('SORT_E03','certificate','확인서 연결 누적량이 확인물량을 초과합니다.'))
    return ('NEEDS_SUPPLEMENT' if issues else 'MATCHED_FOR_REVIEW'),issues


def assess(conn,org,line,entry):
    issues=[]
    if not entry:
        return dict(status='UNVERIFIED',fulfillment_status='MISSING',certificate_status='MISSING',actual_shipped=None,issues=issues)
    data=entry['data']
    if data['action']=='REVOKE':
        intact=s.digest({k:data.get(k) for k in INPUT_FIELDS})==data['payload_hash']
        return dict(status='UNVERIFIED' if intact else 'NEEDS_SUPPLEMENT',fulfillment_status='MISSING',certificate_status='MISSING',
                    actual_shipped=None,record_id=entry['id'],actor=data['actor'],time=data['time'],reason=data['reason'],
                    identity_assurance='LOCAL_UNVERIFIED',issues=[] if intact else [s.problem('SORT_E08','review_record','철회 검토기록 무결성 오류')])
    batch=s.scoped(conn,s.batches,org,line['batch'])
    facility=s.scoped(conn,s.facilities,org,batch['facility'])
    source=s.scoped(conn,store.declarations,org,line['declaration'])
    valid=True
    if s.digest({k:data.get(k) for k in INPUT_FIELDS})!=data['payload_hash']:
        issues.append(s.problem('SORT_E08','review_record','증빙 검토 입력 기록의 무결성 오류'))
        valid=False
    if source['deleted'] or s.digest(source['data'])!=data['source_hash'] or source['revision']!=data['source_revision'] or s.digest(batch['data'])!=data['batch_hash'] or s.digest(facility['data'])!=data['facility_hash']:
        issues.append(s.problem('SORT_E08','linked_source','연결 당시 원장·작업·시설이 변경되어 재검토가 필요합니다.'))
        valid=False
    for snapshot in data['evidence_snapshots']:
        ev=conn.execute(s.evidence.select().where(s.evidence.c.org==org,s.evidence.c.id==snapshot['id'])).mappings().first()
        if ev is None or ev['sha']!=snapshot['sha'] or hashlib.sha256(ev['content']).hexdigest()!=snapshot['sha']:
            issues.append(s.problem('SORT_E08','linked_evidence','연결 증빙 무결성 오류'))
            valid=False
    fulfillment='MISSING'
    if data.get('fulfillment_evidence_id'):
        fulfillment='AUXILIARY_ONLY'
        if data.get('fulfillment_kind') in PRIMARY:
            fulfillment='NEEDS_SUPPLEMENT'
            actual=rules.day(data['actual_shipped'])
            if actual:
                fulfillment='RECORDED_FOR_REVIEW'
                declared=rules.day(source['data'].get('declared'))
                if declared and (actual.year,actual.month)!=(declared.year,declared.month):
                    issues.append(s.problem('SORT_W08','actual_shipped','신고일과 실제 수출일의 귀속월이 다릅니다. 귀속 정책 검토 필요','WARNING'))
                for field in ('work_date','dispatch_date'):
                    before=rules.day(batch['data'].get(field))
                    if before and actual<before:
                        issues.append(s.problem('SORT_W08','actual_shipped','실제 수출일이 선별일 또는 출고일보다 앞섭니다.','WARNING'))
                        fulfillment='NEEDS_SUPPLEMENT'
    certificate_status,certificate_issues=certificate_assessment(conn,org,line,data,batch,facility)
    issues.extend(certificate_issues)
    matched=valid and fulfillment=='RECORDED_FOR_REVIEW' and certificate_status=='MATCHED_FOR_REVIEW'
    return dict(status='LINKED_FOR_REVIEW' if matched else 'NEEDS_SUPPLEMENT',
                fulfillment_status=fulfillment if valid else 'NEEDS_SUPPLEMENT',
                certificate_status=certificate_status if valid else 'NEEDS_SUPPLEMENT',
                actual_shipped=data.get('actual_shipped') if valid and fulfillment=='RECORDED_FOR_REVIEW' else None,
                recorded_actual_shipped=data.get('actual_shipped'),record_id=entry['id'],actor=data['actor'],time=data['time'],
                identity_assurance='LOCAL_UNVERIFIED',reason=data['reason'],issues=issues,
                certificate_id=data.get('certificate_id'),fulfillment_evidence_id=data.get('fulfillment_evidence_id'))


def record(conn,org,claim_id,line_id,data,actor):
    if data['action'] not in {'RECORD','REVOKE'}:
        s.reject('SORT_E08','action','지원되는 증빙 검토 동작이 아닙니다.',422)
    payload_hash=s.digest(dict(data,claim_id=claim_id,line_id=line_id))
    events=history(conn,org)
    for old in events:
        if old['data']['request_key']==data['request_key']:
            if old['data']['payload_hash']!=payload_hash:
                s.reject('SORT_E09','request_key','같은 증빙 검토 요청키의 내용이 다릅니다.')
            return dict(id=old['id'],revision=old['data']['result_revision'])
    claim=s.scoped(conn,s.claims,org,claim_id)
    line=s.scoped(conn,s.allocations,org,line_id)
    if line['claim']!=claim_id or not line['active'] or claim['state'] in {'WITHDRAWN','REJECTED'} or claim['revision']!=data['revision']:
        s.reject('SORT_E08','revision','신청행·예약 상태·최신 버전을 확인하세요.')
    source=s.scoped(conn,store.declarations,org,line['declaration'])
    batch=s.scoped(conn,s.batches,org,line['batch'])
    facility=s.scoped(conn,s.facilities,org,batch['facility'])
    snapshots=[]
    certificate_hash=None
    certificate_evidence_sha=None
    if data['action']=='RECORD':
        if not data.get('fulfillment_evidence_id') and not data.get('certificate_id'):
            s.reject('SORT_E08','evidence_id','수출이행 증빙 또는 확인서를 연결하세요.',422)
        if source['deleted'] or source['revision']!=line['data']['source']['revision'] or s.digest(source['data'])!=line['data']['source']['hash']:
            s.reject('SORT_E08','source','신고가 변경되었습니다. 기존 신청을 보존하고 유효 원본으로 다시 매칭하세요.')
        if data.get('actual_shipped') and (not rules.day(data['actual_shipped']) or rules.day(data['actual_shipped']).isoformat()!=data['actual_shipped']):
            s.reject('SORT_W02','actual_shipped','실제 수출일은 YYYY-MM-DD 형식이어야 합니다.',422)
        if data.get('fulfillment_evidence_id'):
            snapshots.append(evidence_snapshot(conn,org,data['fulfillment_evidence_id']))
            if data.get('fulfillment_kind') not in PRIMARY|{'TRANSPORT_AUXILIARY'}:
                s.reject('SORT_W02','fulfillment_kind','실제 이행 증빙 종류를 확인하세요.',422)
            if data['fulfillment_kind']=='TRANSPORT_AUXILIARY' and data.get('actual_shipped'):
                s.reject('SORT_W02','actual_shipped','운송 보조서류만으로 실제 수출일을 확정할 수 없습니다.',422)
        elif data.get('actual_shipped') or data.get('fulfillment_kind'):
            s.reject('SORT_W02','fulfillment_evidence_id','날짜 및 종류에 대응하는 증빙이 필요합니다.',422)
        if data.get('certificate_id'):
            certificate=s.scoped(conn,s.records,org,data['certificate_id'])
            if certificate['kind']!='CERTIFICATE':
                s.reject('SORT_E08','certificate_id','사무국 확인서 기록을 선택하세요.',422)
            snapshot=evidence_snapshot(conn,org,certificate['data']['evidence_id'])
            if snapshot['sha']!=certificate['data']['evidence_hash']:
                s.reject('SORT_E08','certificate','확인서 원본 해시가 변경되었습니다.')
            snapshots.append(snapshot)
            certificate_hash=s.digest(certificate['data'])
            certificate_evidence_sha=snapshot['sha']
    elif any(data.get(k) for k in ('actual_shipped','fulfillment_kind','fulfillment_evidence_id','certificate_id')):
        s.reject('SORT_E08','action','연결 철회에는 새 증빙값을 함께 전달하지 마세요.',422)
    value=dict(data,claim_id=claim_id,line_id=line_id,payload_hash=payload_hash,actor=actor,time=store.now(),
               source_hash=s.digest(source['data']),source_revision=source['revision'],batch_hash=s.digest(batch['data']),
               facility_hash=s.digest(facility['data']),evidence_snapshots=snapshots,certificate_hash=certificate_hash,
               certificate_evidence_sha=certificate_evidence_sha,
               result_revision=claim['revision']+1,identity_assurance='LOCAL_UNVERIFIED')
    previous_event=next((e for e in reversed(events) if e['data']['line_id']==line_id),None)
    previous=previous_event['id'] if previous_event else None
    value['previous_id']=previous
    entity_id=conn.execute(s.records.insert().values(org=org,kind=KIND,revision=previous_event['revision']+1 if previous_event else 1,data=value)).inserted_primary_key[0]
    assessment=assess(conn,org,line,dict(id=entity_id,data=value))
    if any(i['code']=='SORT_E03' for i in assessment['issues']):
        s.reject('SORT_E03','certificate','확인서 연결 누적량이 확인물량을 초과합니다.')
    conn.execute(s.claims.update().where(s.claims.c.id==claim_id).values(revision=value['result_revision']))
    store.log(conn,org,actor,'sorting_line_evidence_review',entity_id,data['reason'],dict(previous_id=previous),
              dict(claim_id=claim_id,line_id=line_id,status=assessment['status'],identity_assurance='LOCAL_UNVERIFIED'))
    return dict(id=entity_id,revision=value['result_revision'],assessment=assessment)
