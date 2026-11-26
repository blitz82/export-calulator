"""Versioned policy drafts; recording a decision never enables operations."""
from datetime import date
import hashlib
from epms import sorting as s,store

KIND='POLICY_DRAFT'


def rows(conn,org):
    return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==KIND).order_by(s.records.c.id)).mappings()]


def register(conn,org,data,actor):
    evidence=s.scoped(conn,s.evidence,org,data['evidence_id'])
    if hashlib.sha256(evidence['content']).hexdigest()!=evidence['sha']:
        s.reject('SORT_E08','evidence','정책 증빙 해시가 변경되었습니다.')
    if any(not data[k].strip() for k in ('product','choice','basis','reason')):
        s.reject('SORT_E07','reason','정책 품목·선택안·근거·사유를 입력하세요.',422)
    for field in ('start','end'):
        try:
            if date.fromisoformat(data[field]).isoformat()!=data[field]:
                raise ValueError()
        except ValueError:
            s.reject('SORT_E07',field,'정책 검토 적용기간을 YYYY-MM-DD로 입력하세요.',422)
    if data['start']>data['end'] or data['policy_id'] not in {p['id'] for p in s.POLICIES}:
        s.reject('SORT_E07','policy','정책 ID와 적용기간을 확인하세요.',422)
    payload=s.digest(data);existing=rows(conn,org)
    for row in existing:
        if row['data']['request_key']==data['request_key']:
            if row['data']['payload_hash']!=payload:
                s.reject('SORT_E08','request_key','동일 정책 요청키의 내용이 다릅니다.')
            return dict(id=row['id'],revision=row['revision'],operational_approval=False)
    previous_id=data.get('previous_id');revision=1
    key=tuple(data[k] for k in ('policy_id','year','product'))
    if previous_id:
        previous=s.scoped(conn,s.records,org,previous_id)
        if previous['kind']!=KIND or previous['revision']!=data.get('previous_revision') or tuple(previous['data'][k] for k in ('policy_id','year','product'))!=key:
            s.reject('SORT_E08','previous_id','같은 정책·연도·품목의 최신 개정본을 선택하세요.')
        if any(r['data'].get('previous_id')==previous_id for r in existing):
            s.reject('SORT_E08','previous_id','이미 개정된 정책입니다.')
        revision=previous['revision']+1
    elif data.get('previous_revision') is not None:
        s.reject('SORT_E08','previous_revision','개정 대상이 없습니다.',422)
    superseded={r['data'].get('previous_id') for r in existing}
    if data['state']=='DRAFT':
        for row in existing:
            v=row['data']
            if row['id'] in superseded or row['id']==previous_id or v['state']!='DRAFT':
                continue
            if tuple(v[k] for k in ('policy_id','year','product'))==key and data['start']<=v['end'] and data['end']>=v['start']:
                s.reject('SORT_E07','period','같은 정책·연도·품목의 최신 초안 적용기간이 겹칩니다.')
    evidence=s.scoped(conn,s.evidence,org,data['evidence_id'])
    value=dict(data,payload_hash=payload,evidence_hash=evidence['sha'],actor=actor,time=store.now(),
        approval_status='POLICY_PENDING',operational_approval=False)
    entity=conn.execute(s.records.insert().values(org=org,kind=KIND,revision=revision,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_policy_draft',entity,data['reason'],None,dict(previous_id=previous_id,revision=revision))
    return dict(id=entity,revision=revision,operational_approval=False)


def listing(conn,org):
    result=rows(conn,org);superseded={r['data'].get('previous_id') for r in result}
    return dict(policies=s.POLICIES,versions=[dict(r,superseded=r['id'] in superseded) for r in result],operational_approval=False)
