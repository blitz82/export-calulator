"""Exact, conservative reference checks; never authorizes expenditure."""
from decimal import Decimal
from datetime import date
import re
from epms import sorting as s, store, rules

CATEGORIES = {'EXPORT_SORTING_LABOR', 'INTERNAL_LABOR', 'DOMESTIC_SORTING', 'PACKAGING', 'TRANSPORT', 'UNCLASSIFIED', 'SERVICE', 'INSURANCE', 'ALLOWANCE'}


def eligibility(data):
    if data['category'] in {'INTERNAL_LABOR','DOMESTIC_SORTING','PACKAGING','TRANSPORT'}:
        return 'EXCLUDED', '명시 제외 비용'
    if data['category']=='EXPORT_SORTING_LABOR' and not data['separated']:
        return 'EXCLUDED', '수출 선별 및 내부인력 비용 미분리'
    if data['category']=='EXPORT_SORTING_LABOR' and data['separated'] and data.get('separation_evidence_id'):
        return 'REFERENCE_CANDIDATE', '증빙 분리 후보; 정상 지급 대사 및 정책심사 필요'
    return 'PENDING', '인정기준 또는 분리 증빙 미확정'


def add_expense(conn,org,data,actor):
    s.scoped(conn,s.facilities,org,data['facility_id'])
    s.scoped(conn,s.evidence,org,data['evidence_id'])
    if data.get('separation_evidence_id'):
        s.scoped(conn,s.evidence,org,data['separation_evidence_id'])
    if data['category'] not in CATEGORIES or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',data['month']):
        s.reject('SORT_E05','category','비용 분류 및 귀속월을 확인하세요.',422)
    s.decimal(data['amount'])
    for row in s.list_rows(conn,s.expenses,org):
        if row['data']['external_id']==data['external_id']:
            if row['data'].get('input_hash')==s.digest(data):
                return row['id']
            s.reject('SORT_E05','external_id','동일 원 비용번호의 내용이 다릅니다.')
    state,reason=eligibility(data)
    entity_id=conn.execute(s.expenses.insert().values(org=org,facility=data['facility_id'],
        data=dict(data,input_hash=s.digest(data),eligibility=state,reason=reason))).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_expense',entity_id,'비용 원본 등록',None,dict(category=data['category'],eligibility=state))
    return entity_id


def record_payment(conn,org,data,actor):
    expense=s.scoped(conn,s.expenses,org,data['expense_id'])
    s.scoped(conn,s.evidence,org,data['evidence_id'])
    amount=s.decimal(data['amount'])
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',data['transaction_id']):
        s.reject('SORT_E05','transaction_id','거래 식별번호를 확인하세요.',422)
    if data['status'] not in {'REQUESTED','SUCCESS','FAILED','CANCELLED'} or not rules.day(data['date']):
        s.reject('SORT_W05','status','이체 상태와 실제 일자를 확인하세요.',422)
    events=[r for r in s.list_rows(conn,s.records,org) if r['kind']=='PAYMENT_HISTORY']
    for old in events:
        if old['data']['transaction_id']==data['transaction_id']:
            if old['data']==data:
                return old['id']
            s.reject('SORT_E05','transaction_id','동일 거래번호의 원본이 다릅니다.')
    predecessor=None
    if data.get('retry_of'):
        predecessor=s.scoped(conn,s.records,org,data['retry_of'])
        if predecessor['kind']!='PAYMENT_HISTORY' or predecessor['data']['expense_id']!=expense['id'] or predecessor['data']['status'] not in {'FAILED','CANCELLED'}:
            s.reject('SORT_W05','retry_of','재이체 원거래의 실패·취소 및 비용 연결을 확인하세요.')
        retried=sum((s.decimal(r['data']['amount']) for r in events if r['data'].get('retry_of')==data['retry_of'] and r['data']['status']=='SUCCESS'),Decimal(0))
        if data['status']=='SUCCESS' and retried+amount>s.decimal(predecessor['data']['amount']):
            s.reject('SORT_E05','retry_of','실패 원거래의 재이체 가능액을 초과했습니다.')
    successful=sum((s.decimal(r['data']['amount']) for r in events if r['data']['expense_id']==expense['id'] and r['data']['status']=='SUCCESS'),Decimal(0))
    if data['status']=='SUCCESS' and successful+amount>s.decimal(expense['data']['amount']):
        s.reject('SORT_E05','amount','정상 이체 누적액이 비용 원금을 초과합니다.')
    entity_id=conn.execute(s.records.insert().values(org=org,kind='PAYMENT_HISTORY',revision=1,data=data)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_payment_history',entity_id,'과거 비용 이체증빙 검토 등록',None,dict(expense_id=expense['id'],status=data['status']))
    return entity_id


def paid_cost(conn,org,expense_id):
    from epms.settlement import paid,number
    expense=s.scoped(conn,s.expenses,org,expense_id)
    if expense['data'].get('settlement'):
        return max(Decimal(0),paid(expense['data'])-number(expense['data']['excluded']))
    expense=s.scoped(conn,s.expenses,org,expense_id)
    if expense['data']['eligibility']!='REFERENCE_CANDIDATE':
        return Decimal(0)
    payments=[r['data'] for r in s.list_rows(conn,s.records,org) if r['kind']=='PAYMENT_HISTORY' and r['data']['expense_id']==expense_id and r['data']['status']=='SUCCESS']
    return min(s.decimal(expense['data']['amount']),sum((s.decimal(p['amount']) for p in payments),Decimal(0)))


def allocate_cost(conn,org,claim_id,expense_id,amount,actor):
    claim=s.scoped(conn,s.claims,org,claim_id)
    expense=s.scoped(conn,s.expenses,org,expense_id)
    if claim['state'] in {'WITHDRAWN','REJECTED'}:
        s.reject('SORT_E05','claim','종료된 검토 신청에 배부할 수 없습니다.')
    available=paid_cost(conn,org,expense_id)
    allocation=s.decimal(amount)
    old=conn.execute(s.expense_allocations.select().where(s.expense_allocations.c.claim==claim_id,s.expense_allocations.c.expense==expense_id)).mappings().first()
    if old:
        if old['active'] and old['amount']==amount:
            return old['id']
        s.reject('SORT_E05','expense','동일 비용 배부가 이미 있습니다.')
    from epms.sorting_paper import lock
    lock(conn,org,claim_id)
    from epms.settlement import used_cost
    used=used_cost(conn,expense_id)
    if allocation<=0 or used+allocation>available:
        s.reject('SORT_E05','amount','비용의 정상 지급 확인액 또는 가용 비용 pool을 초과했습니다.')
    lines=[r for r in s.list_rows(conn,s.allocations,org) if r['claim']==claim_id and r['active']]
    facilities={s.scoped(conn,s.batches,org,l['batch'])['facility'] for l in lines}
    if expense['facility'] not in facilities:
        s.reject('SORT_E05','facility','비용과 신청 작업의 시설이 다릅니다.')
    entity_id=conn.execute(s.expense_allocations.insert().values(org=org,claim=claim_id,expense=expense_id,amount=amount,active=1)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_cost_allocation',entity_id,'검토 비용 배부',None,dict(claim=claim_id,expense=expense_id,amount=amount))
    return entity_id


def calculate_strategy(scope,strategy,q,u,c=None,rate=None):
    if scope!='TEST':
        s.operation_guard()
    quantity,unit=s.decimal(q),s.decimal(u)
    limit=s.exact_product(quantity,unit)
    if strategy not in {'FIXED_FINAL_RATE','FIXED_COST_RATE','COST_BASE_CAP','SUBSIDY_CAP'}:
        s.reject('SORT_E07','strategy','미승인 혼합형 또는 알 수 없는 시험 산식입니다.',422)
    if strategy=='FIXED_FINAL_RATE':
        return str(limit)  # Unit price already includes subsidy rate.
    if rate is None:
        return None
    r=s.decimal(rate)
    if r>1:
        s.reject('SORT_E07','rate','시험 보조율은 0~1입니다.',422)
    if strategy=='FIXED_COST_RATE':
        return str(s.exact_product(quantity,unit,r))
    if c is None:
        return None
    cost=s.decimal(c)
    if strategy=='COST_BASE_CAP':
        return str(s.exact_product(min(cost,limit),r))
    if strategy=='SUBSIDY_CAP':
        return str(min(s.exact_product(cost,r),limit))
    s.reject('SORT_E07','strategy','알 수 없는 시험 산식입니다.',422)


def form_amount(total,unit_price,benefits):
    """Benefit IDs identify the same physical portion across B/C/D/E."""
    quantity=s.decimal(total)
    if benefits is None or set(benefits)!={'B','C','D','E'} or any(v is None for v in benefits.values()):
        return dict(A=total,F=None,application_limit=None,issues=['SORT_W07'])
    portions={}
    for category,items in benefits.items():
        for item in items:
            amount=s.decimal(item['quantity'])
            if item['portion_id'] in portions and portions[item['portion_id']]!=amount:
                s.reject('SORT_E04','benefits','동일 물량 ID의 차감량이 충돌합니다.')
            portions[item['portion_id']]=amount
    excluded=sum(portions.values(),Decimal(0))
    if excluded>quantity:
        s.reject('SORT_E04','benefits','중복지원 제외량이 전체 물량을 초과합니다.')
    remaining=quantity-excluded
    return dict(A=total,F=str(remaining),application_limit=str(s.exact_product(remaining,s.decimal(unit_price))),reference_review_amount=None,
                approved_amount=None,payable_amount=None,paid_amount=None)


def payable_test(scope,recognized,other_commitments,budget,previous_confirmed='0'):
    if scope!='TEST':
        s.operation_guard()
    recognized,budget,previous=s.decimal(recognized),s.decimal(budget),s.decimal(previous_confirmed)
    # Each claim occupies the ledger once; reserved/approved/paid are state views.
    commitments={}
    for event in other_commitments:
        value=s.decimal(event['amount'])
        commitments[event['claim_id']]=max(commitments.get(event['claim_id'],Decimal(0)),value)
    used=sum(commitments.values(),Decimal(0))
    return dict(payable=str(max(Decimal(0),min(recognized-used,budget))),
                adjustment_candidate=str(min(Decimal(0),recognized-previous)))
