"""Hypothetical facility/product/work-month caps; no budget reservations or approvals."""
from decimal import Decimal,localcontext
from epms import sorting as s,sorting_finance as finance,sorting_cost_review


def claim_group(conn,org,claim):
    lines=[r for r in s.list_rows(conn,s.allocations,org) if r['claim']==claim['id'] and r['active']]
    groups=set()
    for line in lines:
        batch=s.scoped(conn,s.batches,org,line['batch']);day=s.rules.day(batch['data'].get('work_date'))
        if day is None:
            s.reject('SORT_E09','work_date','그룹 작업일을 확인하세요.',422)
        groups.add((batch['facility'],claim['data']['product'],day.strftime('%Y-%m'),claim['data']['year']))
    return groups


def calculate(conn,org,data):
    if len({c['id'] for c in data['claims']})!=len(data['claims']):
        s.reject('SORT_E09','claims','같은 신청을 중복 선택할 수 없습니다.',422)
    active=[r for r in s.list_rows(conn,s.claims,org) if r['state'] not in {'WITHDRAWN','REJECTED'}]
    by_id={r['id']:r for r in active};selected=[];keys=set()
    for item in data['claims']:
        claim=s.scoped(conn,s.claims,org,item['id'])
        if claim['revision']!=item['revision'] or claim['id'] not in by_id:
            s.reject('SORT_E08','claim','신청 상태·개정이 변경되었습니다.')
        groups=claim_group(conn,org,claim)
        if len(groups)!=1:
            s.reject('SORT_E09','group','복수 시설·월 신청은 행별 비용 귀속 기준이 없어 자동 분할할 수 없습니다.',422)
        keys.update(groups);selected.append(claim['id'])
    participants={key:[] for key in keys}
    for claim in active:
        if (claim['data']['product'],claim['data']['year']) not in {(k[1],k[3]) for k in keys}:
            continue
        groups=claim_group(conn,org,claim)
        overlap=groups&keys
        if not overlap:
            continue
        if len(groups)!=1:
            s.reject('SORT_E09','group','같은 범위에 복수 시설·월 신청이 있어 비용 귀속 검토가 필요합니다.')
        participants[next(iter(overlap))].append(claim)
    results=[];source=[];all_costs=s.list_rows(conn,s.expense_allocations,org)
    with localcontext() as context:
        context.prec=60
        price=s.decimal(data['unit_price']);recognized=Decimal(0);unknown=False
        for key,claims in sorted(participants.items()):
            quantity=Decimal(0);cost=Decimal(0);missing=False;issues=[]
            cost_checks={r['id']:r for r in sorting_cost_review.review(conn,org,key[2],key[2],key[0])['rows']}
            for claim in claims:
                for line in s.list_rows(conn,s.allocations,org):
                    if line['claim']==claim['id'] and line['active']:
                        s.physical(conn,org,line['declaration'],line['data']['source']['revision'])
                view=s.review(conn,org,claim['id'])
                if any(i['code']=='SORT_E08' and i['field'] in {'source','evidence'} for i in view['issues']):
                    s.reject('SORT_E08','source','신고·증빙 변경이 있는 신청은 먼저 변경 검토가 필요합니다.')
                quantity+=Decimal(view['quantity'])
                rows=[r for r in all_costs if r['claim']==claim['id'] and r['active']]
                if not rows:
                    missing=True
                for row in rows:
                    expense=s.scoped(conn,s.expenses,org,row['expense'])
                    paid=finance.paid_cost(conn,org,expense['id'])
                    used=sum((s.decimal(r['amount']) for r in all_costs if r['expense']==expense['id'] and r['active']),Decimal(0))
                    if used>paid:
                        s.reject('SORT_E05','cost','비용 pool의 활성 배부가 정상 이체 기록을 초과합니다.')
                    if expense['facility']!=key[0] or expense['data']['month']!=key[2]:
                        missing=True;issues.append('비용 귀속월·시설과 작업 그룹이 달라 자동 안분하지 않음')
                    elif cost_checks[expense['id']]['issues']:
                        s.reject('SORT_E08','cost','비용 대사의 무결성·배부 보완 사항을 먼저 확인하세요.')
                    cost+=s.decimal(row['amount'])
                source.append(dict(claim_id=claim['id'],revision=claim['revision'],review_hash=s.digest(view)))
                issues.extend(i['message'] for i in view['issues'])
            limit=s.exact_product(quantity,price);amount=None if missing else min(cost,limit)
            if amount is None:
                unknown=True
            else:
                recognized+=amount
            results.append(dict(facility_id=key[0],product=key[1],month=key[2],year=key[3],claim_ids=[c['id'] for c in claims],
                selected_claim_ids=[c['id'] for c in claims if c['id'] in selected],quantity=str(quantity),cost=None if missing else str(cost),
                quantity_limit=str(limit),reference_amount=None if amount is None else str(amount),issues=sorted(set(issues))))
        portions={};deduction=None
        if data['commitments'] is not None:
            for item in data['commitments']:
                value=s.decimal(item['amount']);identity=item['portion_id']
                if identity in portions and portions[identity]!=value:
                    s.reject('SORT_E04','portion_id','같은 차감 부분 ID의 금액이 다릅니다.')
                portions[identity]=value
            deduction=sum(portions.values(),Decimal(0))
        budget=None if data['available_budget'] is None else s.decimal(data['available_budget'])
        balance=None if unknown or deduction is None else recognized-deduction
        proposal=None if balance is None or budget is None else max(Decimal(0),min(balance,budget))
    return dict(label='가상 그룹 누적·예산 모의계산 / 운영 예산 예약 아님',groups=results,source=source,source_hash=s.digest(source),
        unit_price=data['unit_price'],reference_total=None if unknown else str(recognized),deduction=None if deduction is None else str(deduction),
        available_budget=None if budget is None else str(budget),reference_balance=None if balance is None else str(balance),
        hypothetical_remaining=None if proposal is None else str(proposal),negative_reference_balance=None if balance is None else str(min(Decimal(0),balance)),
        approved_amount=None,payable_amount=None,operational_approval=False,
        basis='선택 신청과 같은 시설·품목·작업월·사업연도의 모든 활성 신청을 함께 계산; 차감 부분 ID 합집합; 예산은 입력한 전체 범위의 가상 가용 예산',
        limitation='단가·차감·예산은 가상 입력이며 외부 중복/정책 승인/실제 지급 검증 아님. 그룹 간·신청별 실제 배분과 원 단위 절사를 시행하지 않음')
