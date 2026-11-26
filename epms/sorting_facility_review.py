"""CAS facility corrections with immutable before/after records, no qualification approval."""
from epms import sorting as s,store


def revise(conn,org,entity,data,actor):
    if not data['reason'].strip():
        s.reject('SORT_E08','reason','시설 변경 사유가 필요합니다.',422)
    previous=s.scoped(conn,s.facilities,org,entity)
    if previous['revision']!=data['revision']:
        s.reject('SORT_E08','revision','시설 개정이 변경되었습니다.')
    value=data['data']
    if any(value[k]!=previous['data'][k] for k in ('designation','product')) or s.rules.business_number(value['member_number'])!=s.rules.business_number(previous['data']['member_number']):
        s.reject('SORT_E01','identity','지정번호·품목·회원이 바뀌는 경우 별도 시설로 등록하세요.',422)
    s.validate_facility(conn,org,value,entity)
    after=dict(value,qualification='POLICY_PENDING')
    change=dict(facility_id=entity,before_revision=previous['revision'],after_revision=previous['revision']+1,
        before=previous['data'],after=after,reason=data['reason'],actor=actor,time=store.now())
    change['seal']=s.digest(change)
    target=conn.execute(s.records.insert().values(org=org,kind='FACILITY_REVISION',revision=previous['revision']+1,data=change)).inserted_primary_key[0]
    conn.execute(s.facilities.update().where(s.facilities.c.id==entity,s.facilities.c.org==org,s.facilities.c.revision==previous['revision']).values(revision=previous['revision']+1,data=after))
    store.log(conn,org,actor,'sorting_facility_revision',entity,data['reason'],dict(revision=previous['revision'],hash=s.digest(previous['data'])),dict(revision=previous['revision']+1,hash=s.digest(after),history_id=target))
    return dict(id=entity,revision=previous['revision']+1,history_id=target,qualification='POLICY_PENDING')


def history(conn,org,entity):
    s.scoped(conn,s.facilities,org,entity)
    return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='FACILITY_REVISION').order_by(s.records.c.id)).mappings() if r['data']['facility_id']==entity]
