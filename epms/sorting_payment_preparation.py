"""Payment preparation reviews; no approval, budget reservation, or bank payment."""
from decimal import Decimal,localcontext
from html import escape
from io import BytesIO
from epms import sorting as s,store,sorting_beneficiaries as beneficiaries

KIND='PAYMENT_PREPARATION_REVIEW'
LABELS={'DRAFT':'준비 작성 중','HOLD':'준비 보완','INPUT_REVIEWED':'준비 입력 검토 완료 (미승인)','CANCELLED':'준비 취소'}


def events(conn,org):
    return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==KIND).order_by(s.records.c.id)).mappings()]


def checked(record):
    value=record['data']
    if s.digest({k:v for k,v in value.items() if k!='seal'})!=value['seal']:s.reject('SORT_E08','preparation','지급 준비 검토본 해시가 다릅니다.')
    return record


def history(conn,org,entity):
    root=s.scoped(conn,s.records,org,entity)
    if root['kind']!=KIND or root['data']['root_id'] is not None:s.reject('SORT_E09','preparation','지급 준비 대장의 최초 ID를 선택하세요.',404)
    return [checked(r) for r in events(conn,org) if r['id']==entity or r['data']['root_id']==entity]


def listing(conn,org,claim):
    claim_row=s.scoped(conn,s.claims,org,claim);rows=events(conn,org);result=[]
    for root in rows:
        if root['data']['claim_id']!=claim or root['data']['root_id'] is not None:continue
        versions=[r for r in rows if r['id']==root['id'] or r['data']['root_id']==root['id']]
        latest=checked(versions[-1]);d=latest['data']
        result.append(dict(id=root['id'],revision=latest['revision'],state=d['state'],statement_id=d['statement_id'],time=d['time']))
    statements=beneficiaries.view(conn,org,claim)['statements']
    return dict(plans=result,statements=statements,claim_revision=claim_row['revision'])


def source_issues(conn,org,value):
    state=beneficiaries.view(conn,org,value['claim_id']);issues=[]
    if state['stale'] or state['latest_id']!=value['snapshot']['attribution_id'] or state['revision']!=value['source_revision']:
        issues.append('명세의 귀속·원본·신청 버전이 현재와 다릅니다. 기존 준비를 보완/취소하고 재검토하세요.')
    if state['state'] in {'WITHDRAWN','REJECTED'}:issues.append('원 신청이 철회·반려되었습니다.')
    return issues


def total(members):
    with localcontext() as precision:
        precision.prec=60
        return None if any(r['prepared_amount'] is None for r in members) else str(sum((s.decimal(r['prepared_amount']) for r in members),Decimal(0)))


def replay(conn,org,claim,data):
    fingerprint=s.digest(dict(claim_id=claim,data=data))
    for row in events(conn,org):
        if row['data']['request_key']==data['request_key']:
            checked(row)
            if row['data']['input_hash']!=fingerprint:s.reject('SORT_E08','request_key','같은 준비 요청키의 내용이 다릅니다.')
            return row
    return None


def append(conn,org,value,revision,actor,reason):
    value=dict(value,actor=actor,time=store.now(),reason=reason)
    value['seal']=s.digest(value)
    entity=conn.execute(s.records.insert().values(org=org,kind=KIND,revision=revision,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_payment_preparation',entity,reason,None,dict(claim_id=value['claim_id'],state=value['state']))
    return dict(id=value['root_id'] or entity,record_id=entity,revision=revision)


def create(conn,org,claim_id,data,actor):
    claim=s.scoped(conn,s.claims,org,claim_id)
    old=replay(conn,org,claim_id,data)
    if old:return dict(id=old['id'],record_id=old['id'],revision=old['revision'])
    if not data['reason'].strip():s.reject('SORT_E09','reason','준비 대장 작성 사유가 필요합니다.',422)
    if claim['revision']!=data['claim_revision']:s.reject('SORT_E08','revision','신청 버전을 다시 확인하세요.')
    snapshot=beneficiaries.statement(conn,org,data['statement_id'])
    if snapshot['claim_id']!=claim_id:s.reject('SORT_E09','statement','다른 신청의 명세입니다.',404)
    value=dict(root_id=None,previous_id=None,claim_id=claim_id,statement_id=data['statement_id'],source_revision=claim['revision'],snapshot=snapshot,
        request_key=data['request_key'],input_hash=s.digest(dict(claim_id=claim_id,data=data)),state='DRAFT',members=[],approved_amount=None,paid_amount=None,operational_approval=False)
    if source_issues(conn,org,value):s.reject('SORT_E08','statement','최신 귀속·신청의 명세 검토본을 먼저 보존하세요.')
    if any(plan['state']!='CANCELLED' for plan in listing(conn,org,claim_id)['plans']):s.reject('SORT_E08','preparation','이 신청에 활성 준비 대장이 있습니다. 기존 대장을 선택하거나 취소하세요.')
    for member_id in sorted({r['member_id'] for r in snapshot['rows']}):
        own=[r for r in snapshot['rows'] if r['member_id']==member_id]
        value['members'].append(dict(member_id=member_id,member_name=own[0]['member_name'],member_number=own[0]['member_number'],
            quantity=beneficiaries.totals(own,'all')[0][1],prepared_amount=None,planned_date=None,documents_reviewed=False,basis=''))
    return append(conn,org,value,1,actor,data['reason'])


def revise(conn,org,entity,data,actor):
    versions=history(conn,org,entity);latest=versions[-1];value=latest['data']
    fingerprint_data=dict(data,preparation_id=entity)
    old=replay(conn,org,value['claim_id'],fingerprint_data)
    if old:return dict(id=entity,record_id=old['id'],revision=old['revision'])
    if value['state']=='CANCELLED' or data['revision']!=latest['revision']:s.reject('SORT_E08','revision','준비 대장 개정/취소 상태를 다시 확인하세요.')
    if not data['reason'].strip():s.reject('SORT_E09','reason','변경 사유가 필요합니다.',422)
    if data['state'] not in LABELS:s.reject('SORT_E07','state','검토용 준비 상태만 기록할 수 있습니다.',403)
    if source_issues(conn,org,value) and data['state'] not in {'HOLD','CANCELLED'}:s.reject('SORT_E08','source','원본 변경 후에는 준비 보완/취소만 기록할 수 있습니다.')
    supplied={r['member_id']:r for r in data['members']};members=[]
    if len(supplied)!=len(data['members']) or set(supplied)!={r['member_id'] for r in value['members']}:s.reject('SORT_E09','members','고정된 명세의 전체 회원을 중복 없이 기록하세요.')
    for original in value['members']:
        row=supplied[original['member_id']];amount=row['prepared_amount']
        if amount is not None:s.decimal(amount)
        if row['planned_date'] is not None and not s.rules.day(row['planned_date']):s.reject('SORT_E09','planned_date','준비 예정일 형식을 확인하세요.',422)
        if amount is not None and not row['basis'].strip():s.reject('SORT_E09','basis','수동 준비액의 근거가 필요합니다.',422)
        if data['state']=='INPUT_REVIEWED' and (amount is None or s.decimal(amount)<=0 or not row['planned_date'] or not row['documents_reviewed'] or not row['basis'].strip()):
            s.reject('SORT_E09','members','입력 검토 완료에는 회원별 양수 준비액·예정일·서류목록 로컬 검토·근거가 필요합니다.')
        members.append(dict(original,**{k:row[k] for k in ('prepared_amount','planned_date','documents_reviewed','basis')}))
    if data['state']=='INPUT_REVIEWED' and (any(s.decimal(r['unassigned_quantity'])!=0 for r in value['snapshot']['remaining']) or any(s.decimal(r['unassigned_amount'])!=0 for r in value['snapshot']['costs'])):
        s.reject('SORT_E09','source','명세의 미귀속 물량·비용을 먼저 검토하세요.')
    update={k:v for k,v in value.items() if k not in {'seal','actor','time','reason'}}
    update.update(root_id=entity,previous_id=latest['id'],state=data['state'],members=members,request_key=data['request_key'],input_hash=s.digest(dict(claim_id=value['claim_id'],data=fingerprint_data)))
    return append(conn,org,update,latest['revision']+1,actor,data['reason'])


def view(conn,org,entity,member_id=None):
    versions=history(conn,org,entity);latest=versions[-1];value=latest['data']
    members=[dict(r) for r in value['members'] if member_id is None or r['member_id']==member_id]
    if member_id is not None and not members:s.reject('SORT_E09','member_id','이 준비 대장의 회원이 아닙니다.',404)
    issues=source_issues(conn,org,value)
    own_lines={r['line_id'] for r in value['snapshot']['rows'] if member_id is None or r['member_id']==member_id}
    basis_issues=[r['message'] for r in value['snapshot']['review_issues'] if r.get('allocation_id') is None or r['allocation_id'] in own_lines]
    return dict(id=entity,record_id=latest['id'],revision=latest['revision'],claim_id=value['claim_id'],statement_id=value['statement_id'],state=value['state'],state_label=LABELS[value['state']],
        members=members,prepared_total=total(members),stale=bool(issues),issues=issues,basis_issues=basis_issues,approved_amount=None,paid_amount=None,operational_approval=False,
        history=[dict(record_id=r['id'],revision=r['revision'],state=r['data']['state'],time=r['data']['time'],reason=r['data']['reason']) for r in versions] if member_id is None else [],
        label='지급 준비 검토용 / 송금·승인·지급 확정 아님')


def workbook(conn,org,entity,member_id=None):
    from openpyxl import load_workbook
    from epms.sorting_books import workbook_bytes
    state=view(conn,org,entity,member_id)
    body=beneficiaries.statement(conn,org,state['statement_id'],member_id)
    book=load_workbook(BytesIO(beneficiaries.workbook(body)));sheet=book.create_sheet('지급 준비 검토 대장',0)
    sheet.append([state['label']]);sheet.append(['대장 ID',str(entity),'개정',str(state['revision']),'상태',state['state_label']])
    sheet.append(['원 명세 검토본',str(state['statement_id']),'현재 원본 변경',str(state['stale'])])
    sheet.append(['회원','사업자번호','귀속량 kg','수동 준비액 원','준비 예정일','서류목록 로컬 검토','준비액 근거','승인액','실제 지급액'])
    for r in state['members']:sheet.append([r['member_name'],r['member_number'],r['quantity'],r['prepared_amount'],r['planned_date'],'기록함' if r['documents_reviewed'] else '미확인',r['basis'],None,None])
    sheet.append(['선택 대상 준비액 합계',state['prepared_total'],'미확인 회원이 있으면 합계 공란'])
    sheet.append(['확정 정책·독립 인증·직무분리·은행·중복·예산 심사는 별도입니다. 준비 취소는 물량 예약을 해제하지 않습니다.'])
    for issue in state['issues']:sheet.append(['원본 보완',issue])
    book.active=0
    return workbook_bytes(book)


def printable(state):
    def text(value):return escape('미확인' if value is None else str(value),quote=True)
    rows=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in [r['member_name'],r['member_number'],r['quantity'],r['prepared_amount'],r['planned_date'],'기록함' if r['documents_reviewed'] else '미확인',r['basis']])+'</tr>' for r in state['members'])
    issues=''.join(f'<li>{text(message)}</li>' for message in state['issues'])
    basis_issues=''.join(f'<li>{text(message)}</li>' for message in state['basis_issues'])
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>지급 준비 검토 대장</title><style>body{{font:12px sans-serif}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}td,th{{border:1px solid #888;padding:6px;overflow-wrap:anywhere}}@page{{size:A4 landscape;margin:12mm}}thead{{display:table-header-group}}tr{{break-inside:avoid}}@media print{{button{{display:none}}}}</style><h1>지급 준비 검토 대장</h1><p><strong>{text(state['label'])}</strong></p><p>대장 {state['id']} · 개정 {state['revision']} · 신청 {state['claim_id']} · 원 명세 {state['statement_id']} · {text(state['state_label'])}</p><p>준비 예정일은 실제 지급일이 아니며 수동 준비액은 승인액이 아닙니다. 입력 검토 완료도 정책·은행·예산·중복지원 심사 완료가 아닙니다.</p><button onclick="window.print()">준비 대장 인쇄/PDF 저장</button><table><thead><tr><th>회원</th><th>사업자번호</th><th>귀속량 kg</th><th>수동 준비액 원</th><th>준비 예정일</th><th>서류목록 로컬 검토</th><th>준비액 근거</th></tr></thead><tbody>{rows}</tbody></table><p>선택 대상 준비액 합계: {text(state['prepared_total'])}원 · 승인액/실제 지급액 미확정</p><ul>{issues}</ul><h2>기준 명세의 보완·보류</h2><ul>{basis_issues}</ul><p>재출력은 지급이 아닙니다. 준비 취소는 신청 물량·비용 예약을 해제하지 않습니다.</p></html>'''
