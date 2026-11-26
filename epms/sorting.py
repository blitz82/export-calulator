"""Review-only sorting ledger. No operational approval or payment authority."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, localcontext

from sqlalchemy import Column, ForeignKey, Integer, JSON, LargeBinary, String, Table, UniqueConstraint, select

from epms import rules, store

VERSION = 'sorting-review-1'
POLICIES = [dict(id=f'P-{i:02}', status='POLICY_PENDING') for i in range(1, 16)]
PROFILES = [dict(id='PACKAGE_2026_REFERENCE', status='REFERENCE_ONLY', year=2026,
                 effective_date=None, subsidy_rate=None, rounding=None),
            dict(id='INTEGRATED_2027_DRAFT', status='SIMULATION_ONLY', year=2027,
                 effective_date=None, subsidy_rate=None, rounding=None)]
MAX_SCALED = 9_000_000_000_000_000_000


def table(name, *columns, **kw):
    return Table('sorting_' + name, store.metadata, Column('id', Integer, primary_key=True),
                 Column('org', ForeignKey('organizations.id'), nullable=False), *columns, **kw)


facilities = table('facilities', Column('revision', Integer, nullable=False), Column('data', JSON, nullable=False))
batches = table('batches', Column('facility', ForeignKey(facilities.c.id), nullable=False),
                Column('revision', Integer, nullable=False), Column('quantity', Integer, nullable=False),
                Column('data', JSON, nullable=False))
evidence = table('evidence', Column('sha', String, nullable=False), Column('name', String, nullable=False),
                 Column('content', LargeBinary, nullable=False), Column('data', JSON, nullable=False),
                 UniqueConstraint('org', 'sha'))
claims = table('claims', Column('revision', Integer, nullable=False), Column('state', String, nullable=False),
               Column('request_key', String, nullable=False), Column('data', JSON, nullable=False),
               UniqueConstraint('org', 'request_key'))
allocations = table('allocations', Column('claim', ForeignKey(claims.c.id), nullable=False),
                    Column('batch', ForeignKey(batches.c.id), nullable=False),
                    Column('declaration', ForeignKey(store.declarations.c.id), nullable=False),
                    Column('logical_key', String, nullable=False), Column('quantity', Integer, nullable=False),
                    Column('active', Integer, nullable=False), Column('data', JSON, nullable=False))
expenses = table('expenses', Column('facility', ForeignKey(facilities.c.id), nullable=False),
                 Column('data', JSON, nullable=False))
expense_allocations = table('expense_allocations', Column('claim', ForeignKey(claims.c.id), nullable=False),
                            Column('expense', ForeignKey(expenses.c.id), nullable=False),
                            Column('amount', String, nullable=False), Column('active', Integer, nullable=False),
                            UniqueConstraint('claim', 'expense'))
records = table('records', Column('kind', String, nullable=False), Column('revision', Integer, nullable=False),
                Column('data', JSON, nullable=False))
TABLES = (facilities, batches, evidence, claims, allocations, expenses, expense_allocations, records)


class SortingError(ValueError):
    def __init__(self, code, field, message, status=409):
        self.code, self.field, self.message, self.status = code, field, message, status
        super().__init__(message)


def reject(code, field, message, status=409):
    raise SortingError(code, field, message, status)


def decimal(value, field='amount', places=6):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,' + str(places) + r'})?', value):
        reject('SORT_E03', field, '음수·빈값·과도한 자릿수 없이 정확한 십진수 문자열을 입력하세요.', 422)
    return Decimal(value)


def exact_product(*values):
    # At most three accepted 19-digit operands; preserve every fractional digit.
    with localcontext() as context:
        context.prec=60
        result=Decimal(1)
        for value in values:
            result*=value
        return result


def kg(value):
    result = int(decimal(value, 'quantity') * 1_000_000)
    if result > MAX_SCALED:
        reject('SORT_E03', 'quantity', '수량 범위를 초과했습니다.', 422)
    return result


def text_quantity(value):
    return str(Decimal(value) / 1_000_000)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def problem(code, field, message, severity='ERROR'):
    return dict(code=code, field=field, message=message, severity=severity,
                blocks_approval=True, explainable=False, resolution=None)


def operation_guard(*_args, **_kwargs):
    # Deliberate deny: this installation has no independent authentication or approved policy.
    reject('SORT_E07', 'operation_mode', '정책 미승인 및 독립 승인통제 미구축: 검토 전용입니다.', 403)


def scoped(conn, target, org, entity_id):
    row = conn.execute(target.select().where(target.c.id == entity_id, target.c.org == org)).mappings().first()
    if row is None:
        reject('SORT_E09', 'id', '현재 조직의 자료를 찾을 수 없습니다.', 404)
    return dict(row)


def list_rows(conn, target, org):
    return [dict(row) for row in conn.execute(target.select().where(target.c.org == org).order_by(target.c.id)).mappings()]


def normalize_number(value):
    return re.sub(r'[\s-]', '', rules.clean(value)).upper()


def physical(conn, org, entity_id, revision):
    row = scoped(conn, store.declarations, org, entity_id)
    data = row['data']
    if row['deleted'] or row['revision'] != revision or rules.excluded_line(data):
        reject('SORT_E02', 'declaration', '신고 원본이 변경·제외되었습니다. 다시 매칭하세요.')
    try:
        key = normalize_number(data['number']) + ':' + str(int(data['line']))
    except (KeyError, ValueError, TypeError):
        reject('SORT_E02', 'declaration', '신고번호와 품목란을 확인하세요.')
    # Global across organizations and versions. Duplicate authoritative rows require human resolution.
    candidates = []
    for other in conn.execute(store.declarations.select().where(store.declarations.c.deleted == 0)).mappings():
        value = other['data']
        if rules.excluded_line(value):
            continue
        try:
            candidate_key = normalize_number(value['number']) + ':' + str(int(value['line']))
        except (KeyError, ValueError, TypeError):
            continue
        if candidate_key == key:
            candidates.append(other)
    if len(candidates) != 1:
        reject('SORT_E02', 'declaration', '동일 신고의 유효 원본이 복수입니다. 개정·조직 간 충돌을 검토하세요.')
    weight = rules.decimal(data.get('kg'))
    if weight is None:
        reject('SORT_E03', 'kg', '신고 순중량을 확인하세요.')
    return row, key, kg(format(weight, 'f'))


def candidates(conn, org, number):
    needle = normalize_number(number)
    result = []
    for row in store.records(conn, store.declarations, org):
        normalized = normalize_number(row.get('number'))
        if needle and (normalized == needle or normalized.startswith(needle)):
            result.append(dict(id=row['id'], revision=row['revision'], number=row.get('number'),
                               line=row.get('line'), version=row.get('version'), kg=row.get('kg'),
                               declared=row.get('declared'), planned_shipped=row.get('shipped'),
                               actual_shipped=None, matching='CANDIDATE_ONLY', product=row.get('product')))
    return result


def capacity(conn,org,declaration_id,declaration_revision,batch_id,dispatch_id=None):
    from epms.sorting_production import batch_committed
    _,key,total=physical(conn,org,declaration_id,declaration_revision)
    batch=scoped(conn,batches,org,batch_id)
    from epms.settlement import used_declaration
    used=sum(conn.execute(select(allocations.c.quantity).where(allocations.c.logical_key==key,allocations.c.active==1)).scalars())+used_declaration(conn,key)
    batch_used=batch_committed(conn,org,batch_id)
    from epms.settlement import group_remaining
    _,group_available=group_remaining(conn,org,batch_id)
    source_available=min(batch['quantity']-batch_used,group_available)
    dispatch_available=None
    if dispatch_id is not None:
        dispatch=scoped(conn,records,org,dispatch_id)
        if dispatch['kind']!='DISPATCH' or dispatch['data']['batch_id']!=batch_id:
            reject('SORT_E03','dispatch','같은 작업의 출고를 선택하세요.',422)
        dispatch_used=sum(r['quantity'] for r in list_rows(conn,allocations,org) if r['active'] and r['data'].get('dispatch_id')==dispatch_id)
        dispatch_available=max(0,kg(dispatch['data']['quantity'])-dispatch_used)
        source_available=dispatch_available if batch_used<=batch['quantity'] else 0
    return dict(declaration_total=text_quantity(total),declaration_reserved=text_quantity(used),
                declaration_available=text_quantity(max(0,total-used)),declaration_overallocated=text_quantity(max(0,used-total)),
                batch_total=text_quantity(batch['quantity']),batch_committed=text_quantity(batch_used),
                batch_available=text_quantity(max(0,min(batch['quantity']-batch_used,group_available))),batch_revision=batch['revision'],
                dispatch_id=dispatch_id,dispatch_available=None if dispatch_available is None else text_quantity(dispatch_available),
                available=text_quantity(max(0,min(total-used,source_available))),
                label='조회 시점 잔량 / 저장 시 서버 재검증')


def validate_facility(conn, org, data, ignore_id=None):
    for field in ('name', 'product', 'member_number', 'designation', 'valid_from', 'valid_to', 'evidence_id'):
        if not data.get(field):
            reject('SORT_E01', field, '시설 필수정보와 지정증빙을 입력하세요.', 422)
    try:
        start, end = date.fromisoformat(data['valid_from']), date.fromisoformat(data['valid_to'])
    except (ValueError, TypeError):
        reject('SORT_E01', 'valid_from', '유효기간은 ISO 날짜여야 합니다.', 422)
    if start > end:
        reject('SORT_E01', 'valid_to', '유효기간 순서를 확인하세요.', 422)
    scoped(conn, evidence, org, data['evidence_id'])
    if not conn.execute(select(store.memberships.c.id).where(store.memberships.c.org == org,
                         store.memberships.c.number == rules.business_number(data['member_number']))).first():
        reject('SORT_E01', 'member_number', '현재 조직의 회원을 선택하세요.', 422)
    for row in list_rows(conn, facilities, org):
        if row['id']==ignore_id:
            continue
        other = row['data']
        if other['designation'] == data['designation'] and start.isoformat() <= other['valid_to'] and end.isoformat() >= other['valid_from']:
            reject('SORT_E01', 'designation', '동일 지정번호의 유효기간이 겹칩니다.')
def add_facility(conn, org, data, actor):
    validate_facility(conn,org,data)
    data = dict(data, qualification='POLICY_PENDING')
    entity_id = conn.execute(facilities.insert().values(org=org, revision=1, data=data)).inserted_primary_key[0]
    store.log(conn, org, actor, 'sorting_facility', entity_id, '시설 검토 등록', None, data)
    return entity_id


def add_batch(conn, org, data, actor):
    facility = scoped(conn, facilities, org, data['facility_id'])
    scoped(conn, evidence, org, data['evidence_id'])
    if data.get('unit') != 'kg':
        reject('SORT_W03', 'unit', '단위 미확정 또는 kg 이외의 수량은 배분할 수 없습니다.', 422)
    if not rules.day(data.get('work_date')) or data.get('product') != facility['data']['product']:
        reject('SORT_E09', 'product', '작업일과 시설 품목을 확인하세요.', 422)
    if data.get('dispatch_date') and not rules.day(data['dispatch_date']):
        reject('SORT_E09','dispatch_date','출고일 형식을 확인하세요.',422)
    quantity = kg(data['quantity'])
    if not quantity:
        reject('SORT_E03', 'quantity', '작업량은 0보다 커야 합니다.', 422)
    for row in list_rows(conn, batches, org):
        if row['facility'] == facility['id'] and row['data'].get('external_id') == data.get('external_id'):
            reject('SORT_E09', 'external_id', '이미 등록된 작업번호입니다.')
    entity_id = conn.execute(batches.insert().values(org=org, facility=facility['id'], revision=1,
                                                    quantity=quantity, data=data)).inserted_primary_key[0]
    store.log(conn, org, actor, 'sorting_batch', entity_id, '작업 검토 등록', None, data)
    return entity_id


def reserve(conn, org, data, actor):
    from epms.sorting_production import batch_committed
    existing = conn.execute(claims.select().where(claims.c.org == org, claims.c.request_key == data['request_key'])).mappings().first()
    payload_hash = digest(data)
    if existing:
        if existing['data']['payload_hash'] != payload_hash:
            reject('SORT_E09', 'request_key', '동일 요청키에 다른 내용이 전달되었습니다.')
        return existing['id']
    if data['profile'] not in {p['id'] for p in PROFILES}:
        reject('SORT_E07', 'profile', '검토 프로파일을 선택하세요.', 422)
    profile = next(p for p in PROFILES if p['id']==data['profile'])
    if profile['year'] != data['year']:
        reject('SORT_E07','year','참조 프로파일과 사업연도가 다릅니다.',422)
    validated = []
    by_key, by_batch, by_dispatch, by_group = {}, {}, {}, {}
    for line in data['lines']:
        declaration, logical_key, total = physical(conn, org, line['declaration_id'], line['declaration_revision'])
        batch = scoped(conn, batches, org, line['batch_id'])
        if batch['revision'] != line['batch_revision']:
            reject('SORT_E08', 'batch', '작업 버전이 변경되었습니다.')
        scoped(conn,evidence,org,batch['data']['evidence_id'])
        quantity = kg(line['quantity'])
        if quantity <= 0:
            reject('SORT_E03', 'quantity', '배분량은 0보다 커야 합니다.', 422)
        from epms.settlement import used_declaration
        used = sum(conn.execute(select(allocations.c.quantity).where(allocations.c.logical_key == logical_key,
                                                                    allocations.c.active == 1)).scalars())+used_declaration(conn,logical_key)
        batch_used = batch_committed(conn,org,batch['id'])
        dispatch_id=line.get('dispatch_id')
        if dispatch_id:
            dispatch=scoped(conn,records,org,dispatch_id)
            if dispatch['kind']!='DISPATCH' or dispatch['data']['batch_id']!=batch['id']:
                reject('SORT_E03','dispatch','출고와 작업 연결이 다릅니다.')
            dispatch_used=sum(r['quantity'] for r in list_rows(conn,allocations,org) if r['active'] and r['data'].get('dispatch_id')==dispatch_id)
            by_dispatch[dispatch_id]=by_dispatch.get(dispatch_id,0)+quantity
            if dispatch_used+by_dispatch[dispatch_id]>kg(dispatch['data']['quantity']):
                reject('SORT_E03','quantity','출고의 배분 가능량을 초과했습니다.')
        by_key[logical_key] = by_key.get(logical_key, 0) + quantity
        by_batch[batch['id']] = by_batch.get(batch['id'], 0) + (0 if dispatch_id else quantity)
        from epms.settlement import group_remaining
        root,remaining=group_remaining(conn,org,batch['id'])
        by_group[root]=by_group.get(root,0)+(0 if dispatch_id else quantity)
        if by_group[root]>remaining:
            reject('SORT_E03','quantity','원집계·분할 기록의 공통 잔량을 초과했습니다.')
        if used + by_key[logical_key] > total or batch_used + by_batch[batch['id']] > batch['quantity']:
            reject('SORT_E03', 'quantity', '신고 순중량 또는 작업 가용량을 초과했습니다.')
        source = dict(id=declaration['id'], revision=declaration['revision'], hash=digest(declaration['data']),
                      original=declaration['data'])
        validated.append((line, logical_key, quantity, source))
    snapshot = dict(payload_hash=payload_hash, profile=data['profile'], year=data['year'],
                    product=data['product'], creator=actor, created=store.now(), external_check='UNKNOWN',
                    policies=POLICIES, approved_amount=None, payable_amount=None, paid_amount=None,
                    label='검토용 / 지급 미확정')
    entity_id = conn.execute(claims.insert().values(org=org, revision=1, state='RESERVED',
                            request_key=data['request_key'], data=snapshot)).inserted_primary_key[0]
    for line, logical_key, quantity, source in validated:
        batch = scoped(conn, batches, org, line['batch_id'])
        source_evidence = scoped(conn,evidence,org,batch['data']['evidence_id'])
        if batch['data']['product'] != data['product']:
            reject('SORT_E09', 'product', '다른 품목의 작업이 혼입되었습니다.')
        conn.execute(allocations.insert().values(org=org, claim=entity_id, batch=line['batch_id'],
                     declaration=line['declaration_id'], logical_key=logical_key, quantity=quantity, active=1,
                     data=dict(source=source, batch_revision=line['batch_revision'],
                               evidence_id=batch['data']['evidence_id'], actual_shipped=None,
                               evidence_sha=source_evidence['sha'],
                               dispatch_id=line.get('dispatch_id'),
                               fulfillment='UNVERIFIED', state='RESERVED')))
    store.log(conn, org, actor, 'sorting_claim', entity_id, '검토 물량 예약', None, snapshot)
    return entity_id


def transition(conn, org, entity_id, revision, action, reason, actor):
    if action not in ('REVIEW', 'SUPPLEMENT', 'SUBMIT', 'WITHDRAW', 'REJECT'):
        operation_guard()
    row = scoped(conn, claims, org, entity_id)
    allowed = {'RESERVED': {'REVIEW', 'WITHDRAW'}, 'REVIEW': {'SUPPLEMENT', 'SUBMIT', 'WITHDRAW', 'REJECT'},
               'SUPPLEMENT': {'REVIEW', 'WITHDRAW'}, 'SUBMITTED': {'REVIEW', 'WITHDRAW', 'REJECT'}}
    if row['revision'] != revision or action not in allowed.get(row['state'], set()):
        reject('SORT_E08', 'state', '신청 버전 또는 허용 상태 전이를 확인하세요.')
    if action in ('WITHDRAW','REJECT'):
        from epms.sorting_paper import lock
        lock(conn,org,entity_id)
    state = {'REVIEW': 'REVIEW', 'SUPPLEMENT': 'SUPPLEMENT', 'SUBMIT': 'SUBMITTED',
             'WITHDRAW': 'WITHDRAWN', 'REJECT': 'REJECTED'}[action]
    conn.execute(claims.update().where(claims.c.id == entity_id).values(state=state, revision=revision+1))
    if action in ('WITHDRAW', 'REJECT'):
        conn.execute(allocations.update().where(allocations.c.claim == entity_id).values(active=0))
        conn.execute(expense_allocations.update().where(expense_allocations.c.claim == entity_id).values(active=0))
    store.log(conn, org, actor, 'sorting_claim', entity_id, reason, dict(state=row['state']), dict(state=state))
    return state


def review(conn, org, entity_id):
    from epms.sorting_finance import paid_cost
    from epms import sorting_evidence_review as linked
    row = scoped(conn, claims, org, entity_id)
    issues = [problem('SORT_E07', 'profile', '2027 정책 및 독립 승인통제 미확정'),
              problem('SORT_W07', 'external_check', '외부 중복원장 미조회', 'WARNING')]
    evidence_reviews=linked.latest(conn,org)
    lines = []
    for allocation in conn.execute(allocations.select().where(allocations.c.claim == entity_id)).mappings():
        source = scoped(conn, store.declarations, org, allocation['declaration'])
        batch=scoped(conn,batches,org,allocation['batch'])
        facility=scoped(conn,facilities,org,batch['facility'])
        original=allocation['data']['source']['original']
        line_issues=[]
        if source['revision'] != allocation['data']['source']['revision'] or digest(source['data']) != allocation['data']['source']['hash'] or source['deleted']:
            line_issues.append(problem('SORT_E08', 'source', '신고 원본 변경: 기존 검토본을 보존하고 조정 검토 필요'))
        ev = scoped(conn, evidence, org, allocation['data']['evidence_id'])
        if hashlib.sha256(ev['content']).hexdigest() != ev['sha'] or ev['sha'] != allocation['data'].get('evidence_sha'):
            line_issues.append(problem('SORT_E08', 'evidence', '증빙 무결성 오류'))
        current_weight=rules.decimal(source['data'].get('kg'))
        original_weight=rules.decimal(original.get('kg'))
        delta=None if current_weight is None or original_weight is None else str(current_weight-original_weight)
        work=rules.day(batch['data'].get('work_date'))
        dispatch=rules.day(batch['data'].get('dispatch_date'))
        if work and not (facility['data']['valid_from']<=work.isoformat()<=facility['data']['valid_to']):
            line_issues.append(problem('SORT_E01','work_date','작업일이 시설 지정기간 밖입니다. 자격 기준일 정책 검토 필요'))
        if work and dispatch and dispatch<work:
            line_issues.append(problem('SORT_W08','dispatch_date','출고일이 선별일보다 앞섭니다. 원본 날짜 의미를 확인하세요.','WARNING'))
        declared=rules.day(source['data'].get('declared'))
        if work and declared and (work.year,work.month)!=(declared.year,declared.month):
            line_issues.append(problem('SORT_W08','declared','선별일과 신고일의 귀속월이 다릅니다. 이월·귀속 정책 검토 필요','WARNING'))
        evidence_review=linked.assess(conn,org,dict(allocation),evidence_reviews.get(allocation['id']))
        line_issues.extend(evidence_review['issues'])
        if evidence_review['fulfillment_status']!='RECORDED_FOR_REVIEW':
            line_issues.append(problem('SORT_W02','actual_shipped','실제 수출이행 근거 미확인 또는 보완 필요; 예정일 대체 불가','WARNING'))
        if evidence_review['certificate_status']!='MATCHED_FOR_REVIEW':
            line_issues.append(problem('SORT_E08','certificate','사무국 확인서 연결 및 시설·회원·기간·물량 대사 필요'))
        if evidence_review['status']=='LINKED_FOR_REVIEW':
            line_issues.append(problem('SORT_W07','reviewer_identity','로컬 검토기록입니다. 독립된 발급·검토자 인증 및 운영 심사는 별도입니다.','WARNING'))
        issues.extend(dict(p,allocation_id=allocation['id']) for p in line_issues)
        lines.append(dict(id=allocation['id'], quantity=text_quantity(allocation['quantity']),
                          declaration_id=allocation['declaration'], batch_id=allocation['batch'],
                          source=allocation['data']['source'], state=allocation['data'].get('state'), active=bool(allocation['active']),
                          reason=allocation['data'].get('reason'),evidence_id=ev['id'],issues=line_issues,
                          current_kg=source['data'].get('kg'),quantity_delta=delta,
                          evidence_review=evidence_review,
                          dates=dict(work_date=batch['data'].get('work_date'),dispatch_date=batch['data'].get('dispatch_date'),
                                     declared=source['data'].get('declared'),planned_shipped=source['data'].get('shipped'),
                                     actual_shipped=evidence_review['actual_shipped'])))
    cost_rows=[r for r in list_rows(conn,expense_allocations,org) if r['claim']==entity_id and r['active']]
    cost=sum((min(decimal(r['amount']),paid_cost(conn,org,r['expense'])) for r in cost_rows),Decimal(0)) if cost_rows else None
    return dict(id=row['id'], revision=row['revision'], state=row['state'], data=row['data'], lines=lines,
                quantity=text_quantity(sum(kg(line['quantity']) for line in lines if line['active'])),
                original_quantity=text_quantity(sum(kg(line['quantity']) for line in lines)), issues=issues,
                reference_cost=None if cost is None else str(cost),
                approved_amount=None, payable_amount=None, paid_amount=None, label='검토용 / 지급 미확정')


def line_transition(conn,org,claim_id,line_id,revision,action,reason,actor):
    claim=scoped(conn,claims,org,claim_id)
    line=scoped(conn,allocations,org,line_id)
    if line['claim']!=claim_id or claim['revision']!=revision or claim['state'] in {'WITHDRAWN','REJECTED'}:
        reject('SORT_E08','line','신청·행·버전을 확인하세요.')
    if action=='REJECT':
        from epms.sorting_paper import lock
        lock(conn,org,claim_id)
    if action not in {'SUPPLEMENT','REVIEW','REJECT'} or not line['active']:
        reject('SORT_E08','line','허용된 행 상태 전이가 아닙니다.')
    data=dict(line['data'],state=action,reason=reason)
    conn.execute(allocations.update().where(allocations.c.id==line_id).values(data=data,active=0 if action=='REJECT' else 1))
    conn.execute(claims.update().where(claims.c.id==claim_id).values(revision=revision+1))
    store.log(conn,org,actor,'sorting_claim_line',line_id,reason,dict(state=line['data'].get('state')),dict(state=action))
    return dict(state=action,revision=revision+1)


def reference_amount(quantity, unit_price, eligible_cost=None):
    limit = exact_product(decimal(quantity, 'quantity'), decimal(unit_price, 'unit_price'))
    cost = None if eligible_cost is None else decimal(eligible_cost, 'eligible_cost')
    return dict(quantity_limit=str(limit), reference_review_amount=None if cost is None else str(min(cost, limit)),
                cost_status='UNVERIFIED' if cost is None else 'VERIFIED_ZERO' if cost == 0 else 'REFERENCE_INPUT',
                approved_amount=None, payable_amount=None, paid_amount=None, subsidy_rate=None,
                label='검토용 / 지급 미확정', policy_status='POLICY_PENDING')


def commit_import(conn, org, evidence_id, sheet, facility_id, product, selected_rows, actor):
    from epms.sorting_books import preview
    source = scoped(conn, evidence, org, evidence_id)
    scoped(conn, facilities, org, facility_id)
    data = preview(source['content'], sheet)
    available = {row['row']:row for row in data['rows']}
    if len(set(selected_rows)) != len(selected_rows) or not selected_rows:
        reject('SORT_E09', 'rows', '서로 다른 원본 행을 선택하세요.', 422)
    result = []
    for number in selected_rows:
        row = available.get(number)
        if row is None or any(i['field'] in ('number','quantity','product','work_date') for i in row['issues']):
            reject('SORT_E09', 'rows', '원본 필수값 오류를 보완한 행만 가져올 수 있습니다.', 422)
        values = row['original']
        if values.get('product') and values['product'] != product:
            reject('SORT_E09', 'product', '다른 품목이 혼입되었습니다.', 422)
        key = f"import:{source['sha']}:{sheet}:{number}"
        existing = next((b for b in list_rows(conn,batches,org) if b['data'].get('external_id')==key),None)
        if existing:
            if existing['facility'] != facility_id or existing['data']['product'] != product:
                reject('SORT_E09','facility','이미 다른 시설·품목으로 가져온 원본 행입니다.')
            result.append(existing['id'])
            continue
        original_date = values.get('work_date','').split(' ')[0]
        if not rules.day(original_date):
            reject('SORT_E09','work_date','작업일 의미와 원본 날짜를 확인하세요.',422)
        result.append(add_batch(conn,org,dict(facility_id=facility_id, external_id=key, work_date=original_date,
            dispatch_date=None,product=product,quantity=values['quantity'],unit='kg',evidence_id=evidence_id,
            source=row,number_candidate=values['number'],note='Excel 원본 등록; 물리 연결 및 적격 미확인'),actor))
    return result
