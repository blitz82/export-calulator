"""Read-only cross-ledger review. Never determines payment eligibility."""
from sqlalchemy import select
from decimal import Decimal
from epms import sorting as s,store,sorting_beneficiaries as beneficiaries
from epms import sorting_payment_preparation as preparation,sorting_contract_review as contracts,sorting_cost_review as costs

CATEGORIES=[dict(id='DOCUMENTS',label='자료 보완'),dict(id='SOURCE',label='원본 재검토'),dict(id='POLICY',label='정책 판단 보류'),dict(id='OPERATIONS',label='운영 조건 미충족')]


def review(conn,org,claim_id,statement_id=None,preparation_id=None):
    claim=s.scoped(conn,s.claims,org,claim_id);items=[];seen=set()
    def add(category,status,title,message,menu,field='',line_id=None,reference=None):
        identity=(category,title,field,line_id,message)
        if identity in seen:return
        seen.add(identity)
        items.append(dict(id=s.digest(identity)[:20],category=category,status=status,title=title,message=message,reference=reference,
            action=dict(menu=menu,line_id=line_id,field=field)))
    def unavailable(title,menu,exc):
        add('SOURCE','UNAVAILABLE',title,'현재 기록을 확인할 수 없습니다: '+exc.message,menu)
    records=s.list_rows(conn,s.records,org)
    statement_choices=[dict(id=r['id'],time=r['data']['time']) for r in records if r['kind']==beneficiaries.STATEMENT and r['data']['claim_id']==claim_id]
    roots=[r for r in records if r['kind']==preparation.KIND and r['data']['claim_id']==claim_id and r['data']['root_id'] is None]
    plans=[]
    for root in roots:
        versions=[r for r in records if r['kind']==preparation.KIND and (r['id']==root['id'] or r['data']['root_id']==root['id'])]
        latest=versions[-1]
        plans.append(dict(id=root['id'],revision=latest['revision'],state=latest['data']['state'],statement_id=latest['data']['statement_id']))
    selected_preparation=None;selected_statement=None
    if preparation_id is not None:
        row=s.scoped(conn,s.records,org,preparation_id)
        if row['kind']!=preparation.KIND or row['data']['claim_id']!=claim_id or row['data']['root_id'] is not None:s.reject('SORT_E09','preparation','현재 신청의 준비 대장을 선택하세요.',404)
        try:selected_preparation=preparation.view(conn,org,preparation_id)
        except s.SortingError as exc:unavailable('준비 대장 무결성','지급 준비 대장',exc)
        if statement_id is None:statement_id=row['data']['statement_id']
    if statement_id is not None:
        row=s.scoped(conn,s.records,org,statement_id)
        if row['kind']!=beneficiaries.STATEMENT or row['data']['claim_id']!=claim_id:s.reject('SORT_E09','statement','현재 신청의 명세를 선택하세요.',404)
        try:selected_statement=beneficiaries.statement(conn,org,statement_id)
        except s.SortingError as exc:unavailable('명세 검토본 무결성','회원별 지급명세',exc)
    if claim['state'] in {'WITHDRAWN','REJECTED'}:
        add('SOURCE','REVIEW_REQUIRED','종료된 신청','철회·반려된 신청입니다. 과거 기록 조회용이며 지급 준비를 계속 진행할 수 없습니다.','신청 검토',reference=claim_id)
    active=[r for r in s.list_rows(conn,s.allocations,org) if r['claim']==claim_id and r['active']]
    if not active:add('DOCUMENTS','NEEDS_SUPPLEMENT','활성 물량 배분','현재 활성 배분행이 없습니다.','수출 매칭·예약')
    result=None
    try:
        result=s.review(conn,org,claim_id)
        for issue in result['issues']:
            field=issue['field']
            if field=='profile':continue # consolidated policy section below
            category='OPERATIONS' if field in {'external_check','reviewer_identity'} else 'SOURCE' if field in {'source','evidence'} else 'POLICY' if field in {'work_date','declared'} else 'DOCUMENTS'
            status='POLICY_PENDING' if category=='POLICY' else 'REVIEW_REQUIRED'
            add(category,status,'신청 검토 근거',issue['message'],'신청 검토',field,issue.get('allocation_id'),issue['code'])
    except s.SortingError as exc:unavailable('신청 원본 조회','신청 검토',exc)
    physical_keys=set()
    for line in active:
        try:
            contract=contracts.assess(conn,org,claim_id,line['id'])
            add('SOURCE' if contract['status']=='REVIEW_REQUIRED' else 'DOCUMENTS','RECORDED_FOR_REVIEW' if contract['status']=='RECORDED_FOR_REVIEW' else 'REVIEW_REQUIRED',
                '행별 계약 연결','계약 연결은 로컬 검토기록입니다.' if not contract['issues'] else ' / '.join(contract['issues']),'신청 검토','contract',line['id'],contract.get('contract_id'))
            if line['logical_key'] not in physical_keys:
                physical_keys.add(line['logical_key'])
                declaration=s.scoped(conn,store.declarations,org,line['declaration'])
                _,key,capacity=s.physical(conn,org,declaration['id'],declaration['revision'])
                reserved=sum(conn.execute(select(s.allocations.c.quantity).where(s.allocations.c.logical_key==line['logical_key'],s.allocations.c.active==1)).scalars())
                if key!=line['logical_key']:s.reject('SORT_E08','source','신고 논리키가 달라 현재 상한을 확정할 수 없습니다.')
                if reserved>capacity:add('SOURCE','REVIEW_REQUIRED','물리 순중량 초과',f'현재 신고 상한 {s.text_quantity(capacity)} kg보다 전체 활성 예약량이 {s.text_quantity(reserved-capacity)} kg 많습니다. 자동 조정하지 않습니다.','신청 검토','source',line['id'])
                else:add('SOURCE','RECORDED_FOR_REVIEW','물리 순중량 비교','현재 로컬 원장의 활성 예약량이 신고 상한 이내입니다. 실물·외부 중복 확인은 별도입니다.','신청 검토','source',line['id'])
        except s.SortingError as exc:unavailable(f'행 {line["id"]} 원본·계약 조회','신청 검토',exc)
    attribution=None
    try:
        attribution=beneficiaries.view(conn,org,claim_id)
        if not attribution['rows']:add('DOCUMENTS','NEEDS_SUPPLEMENT','회원별 귀속','저장된 회원 귀속 기록이 없습니다.','회원별 지급명세')
        if attribution['stale']:add('SOURCE','REVIEW_REQUIRED','귀속 원본 변경','귀속 등록 후 원본·회원·비용 등 기준이 변경되었습니다. 새 버전을 검토하세요.','회원별 지급명세')
        for line in attribution['remaining']:
            if Decimal(line['unassigned_quantity'])!=0:add('DOCUMENTS','NEEDS_SUPPLEMENT','미귀속 물량',f'행 {line["line_id"]} 미귀속량 {line["unassigned_quantity"]} kg를 확인하세요.','회원별 지급명세',line_id=line['line_id'])
        for cost in attribution['costs']:
            if Decimal(cost['unassigned_amount'])!=0:add('DOCUMENTS','NEEDS_SUPPLEMENT','미귀속 비용',f'비용 {cost["expense_id"]}의 미귀속액 {cost["unassigned_amount"]}원을 확인하세요.','회원별 지급명세',reference=cost['expense_id'])
        if attribution['rows'] and not attribution['stale']:add('DOCUMENTS','RECORDED_FOR_REVIEW','회원 귀속 기록','회원 귀속의 로컬 기록이 있습니다. 수혜 자격·생산자 동일성 승인은 별도입니다.','회원별 지급명세',reference=attribution['latest_id'])
    except s.SortingError as exc:unavailable('귀속 원장 조회','회원별 지급명세',exc)
    linked_costs=[r for r in s.list_rows(conn,s.expense_allocations,org) if r['claim']==claim_id and r['active']]
    if not linked_costs:add('DOCUMENTS','NEEDS_SUPPLEMENT','비용 배부 미확인','활성 비용 배부가 없습니다. 적격비용 0원으로 판정하지 않습니다.','비용 대사')
    else:
        try:
            ids={r['expense'] for r in linked_costs};expenses=[s.scoped(conn,s.expenses,org,e) for e in ids]
            months=[r['data']['month'] for r in expenses]
            for cost in costs.review(conn,org,min(months),max(months))['rows']:
                if cost['id'] not in ids:continue
                status='RECORDED_FOR_REVIEW' if not cost['issues'] and cost['eligibility']=='REFERENCE_CANDIDATE' and s.decimal(cost['candidate'])>0 else 'REVIEW_REQUIRED'
                add('SOURCE' if cost['issues'] else 'DOCUMENTS',status,'비용·이체 대사',f'비용 {cost["id"]}: '+(' / '.join(cost['issues']) if cost['issues'] else cost['reason']+' · 정상 이체 기록 후보이며 은행 확인 아님'),'비용 대사',reference=cost['id'])
        except s.SortingError as exc:unavailable('비용 원본 조회','비용 대사',exc)
    if selected_statement:
        stale=attribution is None or attribution['stale'] or attribution['latest_id']!=selected_statement['attribution_id']
        add('SOURCE','REVIEW_REQUIRED' if stale else 'RECORDED_FOR_REVIEW','선택 명세와 현재 귀속','선택한 명세가 현재 귀속과 다르거나 원본 재검토가 필요합니다.' if stale else '현재 귀속에 대응하는 고정 명세입니다. 승인본이 아닙니다.','회원별 지급명세',reference=statement_id)
    elif statement_id is None:add('DOCUMENTS','UNVERIFIED' if statement_choices else 'NEEDS_SUPPLEMENT','명세 검토본 선택','보존된 명세를 선택하세요.' if statement_choices else '명세 검토본을 먼저 보존하세요.','회원별 지급명세')
    if selected_preparation:
        plan=selected_preparation
        if statement_id!=plan['statement_id']:add('SOURCE','REVIEW_REQUIRED','명세·준비 대장 불일치','선택한 명세와 준비 대장의 기준 명세가 다릅니다. 같은 기준으로 다시 선택하세요.','지급 준비 대장',reference=preparation_id)
        if plan['stale'] or plan['state']=='CANCELLED':add('SOURCE','REVIEW_REQUIRED','준비 대장 재검토','준비가 취소되었거나 기준 원본이 변경되었습니다.','지급 준비 대장',reference=preparation_id)
        for member in plan['members']:
            missing=[]
            if member['prepared_amount'] is None:missing.append('수동 준비액 미확인')
            elif s.decimal(member['prepared_amount'])==0:missing.append('준비액 0원 별도 검토')
            if not member['planned_date']:missing.append('예정일 미입력')
            if not member['documents_reviewed']:missing.append('서류목록 로컬 검토 미기록')
            if not member['basis'].strip():missing.append('준비 근거 미입력')
            add('DOCUMENTS','NEEDS_SUPPLEMENT' if missing else 'RECORDED_FOR_REVIEW','회원별 준비 입력',f'회원 ID {member["member_id"]}: '+(' / '.join(missing) if missing else '준비 입력 기록이 있습니다. 승인·실제 지급 확인 아님'),'지급 준비 대장',reference=member['member_id'])
    elif preparation_id is None:add('DOCUMENTS','UNVERIFIED' if plans else 'NEEDS_SUPPLEMENT','지급 준비 대장 선택','준비 대장을 선택하세요.' if plans else '명세를 기준으로 지급 준비 검토 대장을 작성하세요.','지급 준비 대장')
    add('POLICY','POLICY_PENDING','P-01~15 확정 정책','지원대상·수혜자·단가·산식·한도·절사·환류·중복 범위 등 확정 정책과 승인 어댑터가 없습니다. 초안 등록은 승인이 아닙니다.','정책 검토',reference=[r['id'] for r in s.POLICIES])
    for title,message,menu in [
        ('전자승인 권한 (미사용)','단일 PC 서면 절차에서는 기관의 실제 결재 문서를 등록합니다. 프로그램이 독립 사용자 인증이나 전자결재를 대신하지 않습니다.','서면 결재·지급'),
        ('외부 예산·결재 절차','예산의 실제 확인·승인은 기관 결재에서 수행합니다. 상신본 연결은 일반 예약 해제를 막지만 운영 예산을 자동 배정하지 않습니다.','서면 결재·지급'),
        ('은행 대사 (외부 절차)','은행에서 실제 송금한 뒤 서면 절차 원장에 기록합니다. 입력된 이체 결과를 프로그램이 은행과 독립 검증한 것은 아닙니다.','서면 결재·지급'),
        ('농가 정산·수혜 자격','농가 정산·수령 주체·원물 귀속의 전수 증빙 및 정책 심사는 외부 결재에서 확인하고 근거를 연결합니다.','비용·거래·생산')]:add('OPERATIONS','OUTSIDE_SYSTEM',title,message,menu)
    from epms import sorting_paper as paper
    cases=paper.listing(conn,org,claim_id)
    if cases:add('OPERATIONS','RECORDED_FOR_REVIEW','서면 절차 근거','이 신청에 고정된 서면 상신본이 있습니다. 결재 문서·실제 지급 기록은 서면 절차 화면에서 확인하세요.','서면 결재·지급',reference=[r['id'] for r in cases])
    categories=[dict(category,attention=sum(r['category']==category['id'] and r['status'] not in {'RECORDED_FOR_REVIEW','OUTSIDE_SYSTEM'} for r in items),recorded=sum(r['category']==category['id'] and r['status']=='RECORDED_FOR_REVIEW' for r in items),outside=sum(r['category']==category['id'] and r['status']=='OUTSIDE_SYSTEM' for r in items)) for category in CATEGORIES]
    return dict(label='지급 준비 종합 점검 / 지급 가능 판정 아님',checked_at=store.now(),claim_id=claim_id,claim_revision=claim['revision'],claim_state=claim['state'],
        quantity=None if result is None else result['quantity'],statement_id=statement_id,preparation_id=preparation_id,statements=statement_choices,preparations=plans,categories=categories,items=items,
        operational_approval=False,payment_eligible=False,approved_amount=None,paid_amount=None,scope='현재 로컬 기록의 읽기 전용 점검. 전체 필수 첨부·현장 실물·은행·타 PC/타 사업 원장 전수 검증을 대신하지 않습니다.')
