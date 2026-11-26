"""Versioned beneficiary attribution and immutable REVIEW ONLY statements."""
import hashlib
from decimal import Decimal, localcontext
from html import escape
from epms import sorting as s, store, sorting_finance as finance, sorting_snapshots

ATTRIBUTION='BENEFICIARY_REVIEW'
STATEMENT='BENEFICIARY_STATEMENT'


def history(conn,org,claim):
    s.scoped(conn,s.claims,org,claim)
    return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==ATTRIBUTION).order_by(s.records.c.id)).mappings() if r['data']['claim_id']==claim]


def context(conn,org,claim,rows):
    body=sorting_snapshots.capture(conn,org,claim,include_beneficiaries=False)
    members=[];proofs=[]
    for member_id in sorted({r['member_id'] for r in rows}):
        member=s.scoped(conn,store.memberships,org,member_id)
        members.append(dict(id=member['id'],number=member['number'],revision=member['revision'],data={k:v for k,v in member['data'].items() if k in {'name','kind','approved','withdrawn'}}))
    for evidence_id in sorted({r['evidence_id'] for r in rows}):
        proof=s.scoped(conn,s.evidence,org,evidence_id)
        proofs.append(dict(id=evidence_id,sha=proof['sha'],actual_sha=hashlib.sha256(proof['content']).hexdigest()))
    # Include global expense commitments; another claim must not hide an overallocated cost.
    expense_ids={cost['expense_id'] for r in rows for cost in r['costs']}
    pools=[r for r in s.list_rows(conn,s.expense_allocations,org) if r['expense'] in expense_ids]
    return dict(source=body,members=members,proofs=proofs,cost_pools=pools)


def checked_rows(conn,org,claim_id,rows):
    lines={r['id']:r for r in s.list_rows(conn,s.allocations,org) if r['claim']==claim_id and r['active']}
    all_costs=s.list_rows(conn,s.expense_allocations,org)
    costs={r['expense']:r for r in all_costs if r['claim']==claim_id and r['active']}
    quantities={};cost_used={};identities=set();result=[];global_costs={}
    for cost in all_costs:
        if cost['active']:global_costs[cost['expense']]=global_costs.get(cost['expense'],Decimal(0))+s.decimal(cost['amount'])
    members={};proofs={};batches={};facilities={};expenses={};paid={}
    def cached(table,cache,entity):
        if entity not in cache:cache[entity]=s.scoped(conn,table,org,entity)
        return cache[entity]
    def proof_check(entity):
        if entity not in proofs:
            proof=s.scoped(conn,s.evidence,org,entity)
            if hashlib.sha256(proof['content']).hexdigest()!=proof['sha']:s.reject('SORT_E08','evidence_id','귀속/비용 근거 파일 무결성을 확인하세요.')
            proofs[entity]=True
    for row in rows:
        line=lines.get(row['line_id'])
        if not line:s.reject('SORT_E09','line_id','활성 신청 배분행을 선택하세요.')
        identity=(row['line_id'],row['member_id'])
        if identity in identities:s.reject('SORT_E09','member_id','같은 배분행·회원은 한 행으로 합쳐 기록하세요.')
        identities.add(identity)
        member=cached(store.memberships,members,row['member_id'])
        proof_check(row['evidence_id'])
        if not row['basis'].strip() or not row['source_reference'].strip():s.reject('SORT_E09','basis','원물/LOT 참조와 귀속 근거를 입력하세요.',422)
        quantity=s.kg(row['quantity'])
        quantities[line['id']]=quantities.get(line['id'],0)+quantity
        if quantity<=0 or quantities[line['id']]>line['quantity']:s.reject('SORT_E03','quantity','회원 귀속량이 배분행 물량을 초과하거나 0입니다.')
        batch=cached(s.batches,batches,line['batch']);facility=cached(s.facilities,facilities,batch['facility'])
        expense_seen=set();row_costs=[]
        for cost in row['costs']:
            expense=cached(s.expenses,expenses,cost['expense_id'])
            if expense['id'] in expense_seen:s.reject('SORT_E05','expense_id','한 귀속행의 동일 비용을 합쳐 입력하세요.')
            expense_seen.add(expense['id'])
            amount=s.decimal(cost['amount']);pool=costs.get(expense['id'])
            cost_used[expense['id']]=cost_used.get(expense['id'],Decimal(0))+amount
            if expense['id'] not in paid:
                paid[expense['id']]=finance.paid_cost(conn,org,expense['id'])
                original={k:v for k,v in expense['data'].items() if k not in {'input_hash','eligibility','reason'}}
                if expense['data'].get('input_hash')!=s.digest(original):s.reject('SORT_E08','costs','비용 원본 입력 해시가 변경되었습니다.')
                proof_check(expense['data']['evidence_id'])
                if expense['data'].get('separation_evidence_id'):proof_check(expense['data']['separation_evidence_id'])
                for event in s.list_rows(conn,s.records,org):
                    if event['kind']=='PAYMENT_HISTORY' and event['data']['expense_id']==expense['id'] and event['data']['status']=='SUCCESS':proof_check(event['data']['evidence_id'])
            if amount<=0 or not pool or expense['facility']!=batch['facility'] or cost_used[expense['id']]>s.decimal(pool['amount']) or global_costs.get(expense['id'],Decimal(0))>paid[expense['id']]:
                s.reject('SORT_E05','costs','시설·신청별 비용 배부/정상 이체 후보 한도를 확인하세요.')
            row_costs.append(dict(cost,month=expense['data']['month'],evidence_id=expense['data']['evidence_id']))
        reference=row.get('reference_amount')
        if reference is not None:s.decimal(reference)
        source=line['data']['source']['original']
        result.append(dict(row,costs=row_costs,member_name=member['data'].get('name',''),member_number=member['number'],facility_id=facility['id'],facility_name=facility['data'].get('name',''),
            batch_id=batch['id'],dispatch_id=line['data'].get('dispatch_id'),work_date=batch['data']['work_date'],product=batch['data']['product'],
            declaration_id=line['declaration'],declaration_number=source.get('number'),declaration_line=source.get('line'),declaration_version=source.get('version'),
            cost_amount=str(sum((s.decimal(c['amount']) for c in row['costs']),Decimal(0))) if row['costs'] else None,approved_amount=None,paid_amount=None))
    return result


def replay(conn,org,kind,claim_id,data):
    fingerprint=s.digest(dict(data,claim_id=claim_id))
    for row in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==kind)).mappings():
        if row['data']['request_key']==data['request_key']:
            if row['data']['input_hash']!=fingerprint:s.reject('SORT_E08','request_key','같은 요청키의 내용이 다릅니다.')
            return dict(row)
    return None


def save(conn,org,claim_id,data,actor):
    s.scoped(conn,s.claims,org,claim_id)
    previous=replay(conn,org,ATTRIBUTION,claim_id,data)
    if previous:return dict(id=previous['id'],revision=previous['data']['result_revision'])
    from epms.sorting_paper import lock
    lock(conn,org,claim_id)
    claim=s.scoped(conn,s.claims,org,claim_id)
    if claim['revision']!=data['revision'] or claim['state'] in {'WITHDRAWN','REJECTED'}:s.reject('SORT_E08','revision','신청 개정/종료 상태를 확인하고 다시 조회하세요.')
    if not data['reason'].strip():s.reject('SORT_E09','reason','귀속 등록·개정 사유가 필요합니다.',422)
    with localcontext() as precision:
        precision.prec=60
        rows=checked_rows(conn,org,claim_id,data['rows'])
    old=history(conn,org,claim_id)
    result_revision=claim['revision']+1
    conn.execute(s.claims.update().where(s.claims.c.id==claim_id).values(revision=result_revision))
    value=dict(claim_id=claim_id,rows=rows,request_key=data['request_key'],input_hash=s.digest(dict(data,claim_id=claim_id)),result_revision=result_revision,
        previous_id=old[-1]['id'] if old else None,reason=data['reason'],actor=actor,time=store.now(),approved=False,identity_assurance='LOCAL_UNVERIFIED')
    value['rows_hash']=s.digest(rows)
    value['source_hash']=s.digest(context(conn,org,claim_id,rows))
    entity=conn.execute(s.records.insert().values(org=org,kind=ATTRIBUTION,revision=len(old)+1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_beneficiary_review',entity,data['reason'],None,dict(claim_id=claim_id,row_count=len(rows)))
    return dict(id=entity,revision=result_revision)


def view(conn,org,claim_id):
    claim=s.scoped(conn,s.claims,org,claim_id);versions=history(conn,org,claim_id)
    members=[dict(id=r['id'],name=r['data'].get('name',''),number=r['number']) for r in conn.execute(store.memberships.select().where(store.memberships.c.org==org).order_by(store.memberships.c.id)).mappings()]
    latest=versions[-1] if versions else None;rows=latest['data']['rows'] if latest else [];issues=[];stale=False
    if latest:
        try:
            stale=s.digest(rows)!=latest['data']['rows_hash'] or s.digest(context(conn,org,claim_id,rows))!=latest['data']['source_hash']
            with localcontext() as precision:
                precision.prec=60
                checked_rows(conn,org,claim_id,rows)
        except s.SortingError as exc:stale=True;issues.append(exc.message)
    if stale:issues.append('귀속 등록 후 신청·회원·증빙·신고·비용 등 원본이 변경되었습니다. 다시 검토하여 새 버전을 저장하세요.')
    quantities={};assigned_costs={}
    for row in rows:
        quantities[row['line_id']]=quantities.get(row['line_id'],0)+s.kg(row['quantity'])
        for cost in row['costs']:assigned_costs[cost['expense_id']]=assigned_costs.get(cost['expense_id'],Decimal(0))+s.decimal(cost['amount'])
    remaining=[]
    for line in s.list_rows(conn,s.allocations,org):
        if line['claim']==claim_id and line['active']:
            batch=s.scoped(conn,s.batches,org,line['batch']);facility=s.scoped(conn,s.facilities,org,batch['facility'])
            remaining.append(dict(line_id=line['id'],quantity=s.text_quantity(line['quantity']),unassigned_quantity=s.text_quantity(line['quantity']-quantities.get(line['id'],0)),
                facility_id=facility['id'],facility_name=facility['data'].get('name',''),declaration_number=line['data']['source']['original'].get('number'),declaration_line=line['data']['source']['original'].get('line')))
    costs=[]
    for item in s.list_rows(conn,s.expense_allocations,org):
        if item['claim']==claim_id and item['active']:
            expense=s.scoped(conn,s.expenses,org,item['expense'])
            costs.append(dict(expense_id=item['expense'],facility_id=expense['facility'],month=expense['data']['month'],amount=item['amount'],unassigned_amount=str(s.decimal(item['amount'])-assigned_costs.get(item['expense'],Decimal(0)))))
    snapshots=[dict(id=r['id'],time=r['data']['time'],attribution_id=r['data']['attribution_id']) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==STATEMENT).order_by(s.records.c.id)).mappings() if r['data']['claim_id']==claim_id]
    review_issues=s.review(conn,org,claim_id)['issues']
    for row in rows:
        for cost in row['costs']:
            if cost['month']!=row['work_date'][:7]:
                review_issues.append(dict(allocation_id=row['line_id'],field='cost_month',message='비용 귀속월과 선별월이 다릅니다. 비용 귀속 근거·정책을 별도 검토하세요.'))
    return dict(claim_id=claim_id,revision=claim['revision'],state=claim['state'],latest_id=latest['id'] if latest else None,rows=rows,members=members,remaining=remaining,costs=costs,review_issues=review_issues,
        stale=stale,issues=issues,history=[dict(id=r['id'],revision=r['revision'],time=r['data']['time'],reason=r['data']['reason']) for r in versions],statements=snapshots,
        approved_amount=None,paid_amount=None,operational_approval=False)


def freeze(conn,org,claim_id,data,actor):
    s.scoped(conn,s.claims,org,claim_id)
    previous=replay(conn,org,STATEMENT,claim_id,data)
    if previous:return dict(id=previous['id'])
    state=view(conn,org,claim_id)
    if state['revision']!=data['revision'] or state['state'] in {'WITHDRAWN','REJECTED'} or state['stale'] or not state['rows']:
        s.reject('SORT_E08','statement','현재 귀속 원장·신청 버전을 재검토하세요.')
    if not data['reason'].strip():s.reject('SORT_E09','reason','명세 보존 사유가 필요합니다.',422)
    claim=s.scoped(conn,s.claims,org,claim_id)
    snapshot=dict(claim_id=claim_id,year=claim['data']['year'],profile=claim['data']['profile'],product=claim['data']['product'],attribution_id=state['latest_id'],
        rows=state['rows'],remaining=state['remaining'],costs=state['costs'],review_issues=state['review_issues'],approved_amount=None,paid_amount=None,label='검토용 / 지급 미확정')
    value=dict(snapshot=snapshot,seal=s.digest(snapshot),claim_id=claim_id,attribution_id=state['latest_id'],request_key=data['request_key'],input_hash=s.digest(dict(data,claim_id=claim_id)),time=store.now(),reason=data['reason'],actor=actor)
    entity=conn.execute(s.records.insert().values(org=org,kind=STATEMENT,revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_beneficiary_statement',entity,data['reason'],None,dict(claim_id=claim_id,attribution_id=state['latest_id']))
    return dict(id=entity)


def statement(conn,org,entity,member_id=None):
    row=s.scoped(conn,s.records,org,entity)
    if row['kind']!=STATEMENT or s.digest(row['data']['snapshot'])!=row['data']['seal']:s.reject('SORT_E08','statement','명세 검토본의 종류·해시를 확인하세요.')
    body=dict(row['data']['snapshot']);body['rows']=[dict(r) for r in body['rows'] if member_id is None or r['member_id']==member_id]
    if member_id is not None and not body['rows']:s.reject('SORT_E09','member_id','해당 명세에 없는 회원입니다.',404)
    body.update(statement_id=entity,created=row['data']['time'],member_copy=member_id is not None)
    # A member copy contains neither other members nor their residual/cost pool information.
    if member_id is not None:
        body.pop('remaining',None);body.pop('costs',None)
        own_lines={r['line_id'] for r in body['rows']}
        body['review_issues']=[r for r in body['review_issues'] if r.get('allocation_id') is None or r['allocation_id'] in own_lines]
    return body


HEADERS=['신고번호','품목란','신고 버전','선별장','선별일','작업 ID','출고 ID','수혜 회원','회원 사업자번호','본인 귀속량 kg','비용 귀속 검토액 원','수동 참고 지원액 원 (미심사)','원물/LOT 참조','귀속 근거','귀속 증빙 ID','승인액','실제 지급액']


def cells(row):
    return [row.get(k) for k in ('declaration_number','declaration_line','declaration_version','facility_name','work_date','batch_id','dispatch_id','member_name','member_number','quantity','cost_amount','reference_amount','source_reference','basis','evidence_id','approved_amount','paid_amount')]


def workbook(body):
    from openpyxl import Workbook
    from epms.sorting_books import workbook_bytes
    book=Workbook();sheet=book.active;sheet.title='검토 지급명세'
    sheet.append(['검토용 / 지급 미확정','공식 지급명세·지급 증빙 아님'])
    sheet.append(['명세 검토본',str(body['statement_id']),'신청',str(body['claim_id']),'연도',str(body['year']),'생성시각',body['created']])
    sheet.append(['수동 참고 지원액은 입력값이며 산식 검증·정책 승인·은행 확인 결과가 아닙니다.'])
    sheet.append(HEADERS)
    for row in body['rows']:sheet.append([None if value is None else str(value) for value in cells(row)])
    total=totals(body['rows'],'all')[0]
    sheet.append(['선택 대상 합계','귀속량 kg',total[1],'비용 검토액 원',total[2],'수동 참고액 원',total[3]])
    cost_sheet=book.create_sheet('비용 배부 근거');cost_sheet.append(['검토용 / 지급 미확정'])
    cost_sheet.append(['수혜 회원','배분행 ID','선별장','선별일','비용 귀속월','비용 ID','귀속 비용 원','비용 증빙 ID'])
    for row in body['rows']:
        for cost in row['costs']:cost_sheet.append([str(value) for value in (row['member_name'],row['line_id'],row['facility_name'],row['work_date'],cost['month'],cost['expense_id'],cost['amount'],cost['evidence_id'])])
    for title,key in [('회원별 소계','member_name'),('시설별 소계','facility_name'),('신고 품목란별 소계','declaration_number')]:
        summary=book.create_sheet(title);summary.append(['검토용 / 지급 미확정']);summary.append(['구분','귀속량 kg','비용 귀속 검토액 원','수동 참고액 합계 원 (미확인 포함 시 공란)'])
        for label,quantity,cost,reference in totals(body['rows'],key):summary.append([label,quantity,cost,reference])
    if not body['member_copy']:
        residual=book.create_sheet('미귀속 물량·비용');residual.append(['배분행 ID','미귀속량 kg'])
        for line in body['remaining']:residual.append([str(line['line_id']),line['unassigned_quantity']])
        residual.append(['비용 ID','미귀속 비용 원'])
        for cost in body['costs']:residual.append([str(cost['expense_id']),cost['unassigned_amount']])
    pending=book.create_sheet('검토 보완·보류');pending.append(['검토용 / 지급 미확정','자료 연결·적격성·외부 확인·정책은 별도 검토'])
    pending.append(['배분행','내용'])
    for issue in body['review_issues']:pending.append([str(issue.get('allocation_id','')),issue['message']])
    return workbook_bytes(book)


def totals(rows,key):
    grouped={}
    with localcontext() as precision:
        precision.prec=60
        for row in rows:
            identity=('all',) if key=='all' else (row['member_id'],) if key=='member_name' else (row['facility_id'],) if key=='facility_name' else (row['declaration_number'],row['declaration_line'],row['declaration_version'])
            label=('선택 대상 합계' if key=='all' else f"{row[key]} / {row['member_number']}" if key=='member_name' else f"{row[key]} / 시설 {row['facility_id']}" if key=='facility_name' else f"{row[key]} / {row['declaration_line']} / 버전 {row['declaration_version']}")
            item=grouped.setdefault(identity,[label,Decimal(0),Decimal(0),Decimal(0)])
            item[1]+=s.decimal(row['quantity'])
            item[2]=None if item[2] is None or row['cost_amount'] is None else item[2]+s.decimal(row['cost_amount'])
            item[3]=None if item[3] is None or row['reference_amount'] is None else item[3]+s.decimal(row['reference_amount'])
    return [[label,str(q),None if c is None else str(c),None if ref is None else str(ref)] for label,q,c,ref in grouped.values()]


def printable(body):
    def text(value):return escape('미확인' if value is None else str(value),quote=True)
    headers=['신고번호 / 품목란 / 버전','선별장 / 선별일','수혜 회원','귀속량 kg','비용 검토액 원','수동 참고액 원 (미심사)','승인액','실제 지급액']
    header=''.join(f'<th>{text(h)}</th>' for h in headers)
    rows=''
    for row in body['rows']:
        values=[f"{row['declaration_number']} / {row['declaration_line']} / {row['declaration_version']}",f"{row['facility_name']} / {row['work_date']}",f"{row['member_name']} / {row['member_number']}"]+[row.get(k) for k in ('quantity','cost_amount','reference_amount','approved_amount','paid_amount')]
        rows+='<tr>'+''.join(f'<td>{text(value)}</td>' for value in values)+'</tr>'
    summaries=''
    for title,key in [('회원별 소계','member_name'),('시설별 소계','facility_name'),('신고 품목란별 소계','declaration_number')]:
        summary=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in values)+'</tr>' for values in totals(body['rows'],key))
        summaries+=f'<h2>{title}</h2><table><thead><tr><th>구분</th><th>귀속량 kg</th><th>비용 검토액 원</th><th>수동 참고액 원</th></tr></thead><tbody>{summary}</tbody></table>'
    provenance=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in [r['line_id'],r['batch_id'],r['dispatch_id'],r['member_name'],r['source_reference'],r['evidence_id'],r['basis']])+'</tr>' for r in body['rows'])
    pending=''.join(f'<li>{text(issue["message"])}</li>' for issue in body['review_issues'])
    cost_rows=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in [r['member_name'],r['line_id'],r['facility_name'],c['month'],c['expense_id'],c['amount'],c['evidence_id']])+'</tr>' for r in body['rows'] for c in r['costs'])
    cost_table=f'<h2>비용 귀속 근거</h2><table><thead><tr><th>회원</th><th>배분행</th><th>시설</th><th>비용 귀속월</th><th>비용 ID</th><th>귀속액 원</th><th>증빙 ID</th></tr></thead><tbody>{cost_rows}</tbody></table>'
    total=totals(body['rows'],'all')[0]
    totals_text=f'<p>선택 대상 합계: {text(total[1])} kg · 비용 검토액 {text(total[2])}원 · 수동 참고액 {text(total[3])}원 (승인·지급 미확정)</p>'
    residual='' if body['member_copy'] else '<h2>미귀속 내역</h2>'+''.join(f'<p>배분행 {text(r["line_id"])}: {text(r["unassigned_quantity"])} kg</p>' for r in body['remaining'])+''.join(f'<p>비용 {text(r["expense_id"])}: {text(r["unassigned_amount"])}원</p>' for r in body['costs'])
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>검토용 지급명세</title><style>body{{font:12px sans-serif}}table{{border-collapse:collapse;width:100%;table-layout:fixed}}td,th{{border:1px solid #888;padding:5px;overflow-wrap:anywhere}}h1{{font-size:20px}}h2{{font-size:15px}}@page{{size:A4 landscape;margin:10mm}}thead{{display:table-header-group}}tr{{break-inside:avoid}}@media print{{button{{display:none}}}}</style><h1>공동선별비 지급명세 검토본</h1><p><strong>검토용 / 지급 미확정 · 공식 지급명세·지급 증빙 아님</strong></p><p>명세 {text(body['statement_id'])} · 신청 {text(body['claim_id'])} · {text(body['year'])} · {text(body['created'])}</p><p>보존 당시 자료입니다. 수동 참고액은 입력값이며 산식 검증·정책 승인·은행 확인 결과가 아닙니다. 미귀속·혼합 원물 근거 및 회원 적격성은 별도 확인해야 합니다.</p><button onclick="window.print()">검토 명세 인쇄/PDF 저장</button><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>{totals_text}{summaries}<h2>물량 귀속 근거</h2><table><thead><tr><th>배분행</th><th>작업</th><th>출고</th><th>회원</th><th>원물/LOT</th><th>증빙 ID</th><th>판단 근거</th></tr></thead><tbody>{provenance}</tbody></table>{cost_table}{residual}<h2>검토 보완·보류</h2><ul>{pending}</ul></html>'''
