"""Historical records and scoped release of unused reservations."""
from decimal import Decimal
from epms import sorting as s,store,sorting_paper as paper

STATES={'RESERVED':'예약','REVIEW':'검토 중','SUPPLEMENT':'보완 중','SUBMITTED':'제출 보관','WITHDRAWN':'예약 해제','REJECTED':'반려'}

def inspect(conn,org,key):
    claim=s.scoped(conn,s.claims,org,key)
    allocations=[r for r in s.list_rows(conn,s.allocations,org) if r['claim']==key]
    costs=[r for r in s.list_rows(conn,s.expense_allocations,org) if r['claim']==key]
    quantity=sum(r['quantity'] for r in allocations if r['active'])
    amount=sum((s.decimal(r['amount']) for r in costs if r['active']),Decimal(0))
    locked=None
    try:paper.lock(conn,org,key)
    except s.SortingError as error:locked=str(error)
    can_release=(any(r['active'] for r in allocations+costs) and claim['state'] in {'RESERVED','REVIEW','SUPPLEMENT','SUBMITTED'} and not locked)
    cases=[]
    for row in paper.records(conn,org,paper.CASE):
        if row['data']['claim_id']!=key:continue
        try:
            paper.checked(row,paper.CASE)
            cases.append(dict(id=row['id'],document_number=row['data'].get('document_number','종전 서면자료'),closed=paper.closed(conn,org,row['id']),report_kind='RESULT' if paper.decisions(conn,org,row['id']) else 'SUBMISSION'))
        except s.SortingError:locked='종전 서면자료의 무결성을 확인해야 합니다.';can_release=False
    return dict(id=key,revision=claim['revision'],state=claim['state'],state_label=STATES.get(claim['state'],'확인 필요'),
        year=claim['data'].get('year'),created=claim['data'].get('created',''),title=claim['data'].get('title') or f"{claim['data'].get('year','')}년 종전 검토자료",
        active_quantity=s.text_quantity(quantity),active_cost=str(amount),can_release=bool(can_release),lock_reason=locked,
        has_active_reservations=any(r['active'] for r in allocations+costs),paper_cases=cases)

def detail(conn,org,key):
    result=inspect(conn,org,key);lines=[];costs=[]
    for row in s.list_rows(conn,s.allocations,org):
        if row['claim']!=key:continue
        batch=s.scoped(conn,s.batches,org,row['batch']);original=row['data'].get('source',{}).get('original',{})
        lines.append(dict(batch_title=batch['data'].get('title') or batch['data'].get('external_id','종전 선별기록'),
            number=original.get('number',''),line=original.get('line',''),quantity=s.text_quantity(row['quantity']),active=bool(row['active'])))
    for row in s.list_rows(conn,s.expense_allocations,org):
        if row['claim']!=key:continue
        expense=s.scoped(conn,s.expenses,org,row['expense'])
        costs.append(dict(title=expense['data'].get('title') or expense['data'].get('external_id','종전 비용'),amount=row['amount'],active=bool(row['active'])))
    return dict(result,lines=lines,costs=costs)

def release(conn,org,key,revision,reason,actor):
    before=inspect(conn,org,key)
    if before['revision']!=revision:s.reject('SORT_E08','revision','종전 예약이 변경되었습니다. 다시 조회하세요.')
    if not reason.strip():s.reject('SORT_E08','reason','예약 해제 사유를 입력하세요.',422)
    if not before['can_release']:s.reject('SORT_E08','archive',before['lock_reason'] or '해제할 활성 예약이 없거나 해제할 수 없는 상태입니다.')
    s.transition(conn,org,key,revision,'WITHDRAW',reason,actor)
    store.log(conn,org,actor,'sorting_archive_release',key,reason,before,dict(state='WITHDRAWN',revision=revision+1))
    return inspect(conn,org,key)
