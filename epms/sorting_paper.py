"""Single-PC external paper decisions and historical bank-result evidence.

This module records externally performed actions; it never approves electronically,
sends money, certifies a bank result, or promotes a draft policy.
"""
import hashlib,re,unicodedata
from decimal import Decimal,localcontext
from html import escape
from io import BytesIO
from epms import sorting as s,store,sorting_beneficiaries as beneficiaries,sorting_payment_preparation as preparation,sorting_snapshots

CASE='PAPER_SUBMISSION'
DECISION='PAPER_DECISION'
PAYMENT='PAPER_BANK_RESULT'
ADJUSTMENT='PAPER_BANK_ADJUSTMENT'
CLOSE='PAPER_SUBMISSION_CLOSE'
KINDS={CASE,DECISION,PAYMENT,ADJUSTMENT,CLOSE}


def records(conn,org=None,kind=None):
    query=s.records.select().where(s.records.c.kind.in_(KINDS))
    if org is not None:query=query.where(s.records.c.org==org)
    if kind:query=query.where(s.records.c.kind==kind)
    return [dict(row) for row in conn.execute(query.order_by(s.records.c.id)).mappings()]


def checked(row,kind=None):
    if row['kind'] not in KINDS or (kind and row['kind']!=kind):s.reject('SORT_E09','paper','서면 절차 원장의 종류가 다릅니다.',404)
    data=row['data']
    if s.digest({k:v for k,v in data.items() if k!='seal'})!=data.get('seal'):s.reject('SORT_E08','paper','서면 원장 무결성을 확인하세요.')
    return row


def get(conn,org,entity,kind=None):return checked(s.scoped(conn,s.records,org,entity),kind)


def money(value):
    number=s.decimal(value)
    if number!=number.to_integral_value():s.reject('SORT_E03','amount','서면 결재·실제 이체 금액은 원 단위 정수로 입력하세요. 계산 원값은 반올림하지 않습니다.',422)
    return number.to_integral_value()


def identifier(value):
    result=re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).upper()
    if not result:s.reject('SORT_E09','reference','문서/계좌 관리참조/거래 식별번호를 입력하세요.',422)
    return result


def proof(conn,org,entity):
    row=s.scoped(conn,s.evidence,org,entity)
    if hashlib.sha256(row['content']).hexdigest()!=row['sha']:s.reject('SORT_E08','evidence','등록 증빙 바이트가 변경되었습니다.')
    return dict(id=entity,sha=row['sha'])


def stored_proof(conn,org,value):
    current=proof(conn,org,value['id'])
    if current['sha']!=value['sha']:s.reject('SORT_E08','evidence','기록 당시 외부 문서 증빙과 현재 파일 해시가 다릅니다.')


def dated(value):
    if not s.rules.day(value):s.reject('SORT_E09','date','유효한 YYYY-MM-DD 일자를 입력하세요.',422)
    return value


def retry(conn,org,kind,data):
    digest=s.digest(dict(kind=kind,input=data))
    for row in records(conn,org):
        if row['data'].get('request_key')==data['request_key']:
            checked(row)
            if row['kind']!=kind or row['data']['input_hash']!=digest:s.reject('SORT_E08','request_key','같은 요청키의 기록 내용이 다릅니다.')
            return row
    return None


def append(conn,org,kind,data,body,actor):
    if not data['reason'].strip():s.reject('SORT_E09','reason','기록 사유가 필요합니다.',422)
    value=dict(body,request_key=data['request_key'],input_hash=s.digest(dict(kind=kind,input=data)),reason=data['reason'],time=store.now(),actor=actor,
        system_approval=False,bank_verified=False)
    value['seal']=s.digest(value)
    entity=conn.execute(s.records.insert().values(org=org,kind=kind,revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_external_paper',entity,data['reason'],None,dict(kind=kind,case_id=body.get('case_id')))
    return dict(id=entity)


def closed(conn,org,case_id):
    return any(checked(r)['data']['case_id']==case_id for r in records(conn,org,CLOSE))


def lock(conn,org,claim_id):
    """Called by ordinary claim/line release and attribution mutation paths."""
    for row in records(conn,org,CASE):
        checked(row,CASE)
        if row['data']['claim_id']==claim_id and not closed(conn,org,row['id']):
            s.reject('SORT_E08','paper_lock','서면 상신/결재·지급 원장에 연결된 신청입니다. 일반 철회·행 해제·귀속 변경은 불가합니다. 서면 절차 화면에서 근거를 확인하세요.')


def current_hash(conn,org,case):
    source=sorting_snapshots.capture(conn,org,case['data']['claim_id'],include_beneficiaries=False)
    attr=beneficiaries.history(conn,org,case['data']['claim_id'])
    prep=preparation.history(conn,org,case['data']['preparation_id'])[-1]
    return s.digest(dict(source=source,attribution_id=attr[-1]['id'] if attr else None,preparation_record_id=prep['id']))


def decisions(conn,org,case_id):return [checked(r) for r in records(conn,org,DECISION) if r['data']['case_id']==case_id]


def adjustments(conn,org,payment_id):return [checked(r) for r in records(conn,org,ADJUSTMENT) if r['data']['payment_id']==payment_id]


def voided(conn,org,payment_id):return any(r['data']['action']=='VOID' for r in adjustments(conn,org,payment_id))


def voided_refunds(conn,org,payment_id):
    return {r['data']['target_adjustment_id'] for r in adjustments(conn,org,payment_id) if r['data']['action']=='VOID_REFUND'}


def balances(conn,org,case_id):
    result={};ever_paid=False;last_refund=0
    for payment in records(conn,org,PAYMENT):
        checked(payment)
        if payment['data']['status']!='SUCCESS':continue
        own=[r for r in payment['data']['lines'] if r['case_id']==case_id]
        if own:ever_paid=True
        if voided(conn,org,payment['id']):continue
        for row in own:
            item=result.setdefault(row['member_id'],dict(paid=Decimal(0),refund=Decimal(0)))
            item['paid']+=money(row['amount'])
        for adjustment in adjustments(conn,org,payment['id']):
            if adjustment['data']['action']!='REFUND' or adjustment['id'] in voided_refunds(conn,org,payment['id']):continue
            for row in adjustment['data']['lines']:
                if row['case_id']!=case_id:continue
                last_refund=max(last_refund,adjustment['id'])
                item=result.setdefault(row['member_id'],dict(paid=Decimal(0),refund=Decimal(0)))
                item['refund']+=money(row['amount'])
    return result,ever_paid,last_refund


def submit(conn,org,data,actor):
    old=retry(conn,org,CASE,data)
    if old:return dict(id=old['id'])
    prep=preparation.view(conn,org,data['preparation_id']);latest=preparation.history(conn,org,data['preparation_id'])[-1]
    if prep['revision']!=data['preparation_revision'] or prep['stale'] or prep['state']=='CANCELLED':s.reject('SORT_E08','preparation','준비 대장의 최신 원본과 개정을 재확인하세요.')
    claim=s.scoped(conn,s.claims,org,prep['claim_id'])
    if claim['state'] in {'WITHDRAWN','REJECTED'}:s.reject('SORT_E08','claim','종료된 신청은 상신할 수 없습니다.')
    lock(conn,org,claim['id'])
    snapshot=latest['data']['snapshot']
    if any(Decimal(r['unassigned_quantity'])!=0 for r in snapshot['remaining']) or any(Decimal(r['unassigned_amount'])!=0 for r in snapshot['costs']):
        s.reject('SORT_E09','source','상신 전에 미귀속 물량·비용을 해소하세요.')
    if not data['policy_basis'].strip():s.reject('SORT_E09','policy_basis','적용 기준 또는 미확정 쟁점을 명시하세요.',422)
    # A reusable external document number must identify one immutable local submission.
    document_key=identifier(data['document_number'])
    if any(r['data']['document_key']==document_key for r in records(conn,org,CASE)):s.reject('SORT_E09','document_number','이미 사용한 상신 문서번호입니다. 재출력은 기존 상신본에서 하세요.')
    body=dict(claim_id=claim['id'],preparation_id=data['preparation_id'],preparation_record_id=latest['id'],document_number=data['document_number'].strip(),document_key=document_key,
        submitted_date=dated(data['submitted_date']),policy_basis=data['policy_basis'],snapshot=snapshot,proposals=latest['data']['members'])
    temporary=dict(data=body)
    body['source_hash']=current_hash(conn,org,temporary)
    return append(conn,org,CASE,data,body,actor)


def decide(conn,org,case_id,data,actor):
    case=get(conn,org,case_id,CASE);old=retry(conn,org,DECISION,dict(data,case_id=case_id))
    if old:return dict(id=old['id'])
    if closed(conn,org,case_id):s.reject('SORT_E08','case','취하된 상신본은 결재 결과를 추가할 수 없습니다.')
    previous=decisions(conn,org,case_id)
    if data['previous_id']!=(previous[-1]['id'] if previous else None):s.reject('SORT_E08','previous_id','최신 결재 기록 ID를 확인하세요.')
    document=proof(conn,org,data['evidence_id']);dated(data['decision_date'])
    if data['decision_date']<case['data']['submitted_date'] and not data['change_note'].strip():s.reject('SORT_E08','change_note','상신일 전 결재입니다. 기존 결재 이관 등 외부 근거를 명시하세요.')
    if not data['decision_basis'].strip():s.reject('SORT_E09','decision_basis','외부 결재의 실제 근거를 입력하세요.',422)
    identity=(identifier(data['document_number']),data['decision_date'],document['sha'])
    duplicate=next((r for r in previous if tuple(r['data']['document_identity'])==identity),None)
    if duplicate and not (data['entry_correction'] and previous[-1]['id']==duplicate['id']):s.reject('SORT_E08','document_number','같은 결재 문서의 재등록입니다. 입력 오기는 최신 기록의 입력 정정으로 남기세요.')
    current=current_hash(conn,org,case)
    if current!=case['data']['source_hash'] and not data['change_note'].strip():s.reject('SORT_E08','change_note','상신 후 원본이 변경되었습니다. 외부 재검토/재결재 근거를 기록하세요.')
    originals={r['member_id']:r for r in case['data']['proposals']};rows={r['member_id']:r for r in data['members']}
    if len(rows)!=len(data['members']) or set(rows)!=set(originals):s.reject('SORT_E09','members','고정 상신본의 전체 회원을 중복 없이 기록하세요.')
    amounts,_,_=balances(conn,org,case_id)
    result=[]
    for member_id,original in originals.items():
        row=rows[member_id];amount=None if row['amount'] is None else money(row['amount'])
        if (row['result']=='APPROVED' and amount is None) or (row['result']!='APPROVED' and amount not in (None,Decimal(0))):s.reject('SORT_E09','amount','가결은 결정액이 필요하고 보류/부결은 금액을 비워야 합니다.')
        net=amounts.get(member_id,dict(paid=Decimal(0),refund=Decimal(0)))
        if (amount or Decimal(0))<net['paid']-net['refund']:s.reject('SORT_E05','amount','기록된 순지급액보다 결재액을 줄일 수 없습니다. 실제 환수 근거부터 기록하세요.')
        proposed=original['prepared_amount']
        if (proposed is None or amount!=s.decimal(proposed)) and not row['difference_reason'].strip():s.reject('SORT_E09','difference_reason','제안액 대비 감액·증액·보류 등의 사유가 필요합니다.')
        result.append(dict(row,amount=None if amount is None else str(amount),member_name=original['member_name'],member_number=original['member_number']))
    return append(conn,org,DECISION,dict(data,case_id=case_id),dict(case_id=case_id,previous_id=data['previous_id'],document_number=data['document_number'].strip(),document_identity=list(identity),decision_date=data['decision_date'],
        proof=document,decision_basis=data['decision_basis'],change_note=data['change_note'],entry_correction=data['entry_correction'],members=result,source_hash=current),actor)


def bank_key(account,date,transaction):return [identifier(account),dated(date),identifier(transaction)]


def payment(conn,org,data,actor):
    old=retry(conn,org,PAYMENT,data)
    if old:return dict(id=old['id'])
    evidence=proof(conn,org,data['evidence_id']);key=bank_key(data['account_reference'],data['paid_date'],data['transaction_id']);amount=money(data['bank_amount'])
    for line in data['lines']:get(conn,org,line['case_id'],CASE)
    if amount<=0:s.reject('SORT_E03','bank_amount','은행 결과 기록 금액은 양수여야 합니다.')
    if data['retry_of'] and data['replaces_payment_id']:s.reject('SORT_E09','payment','재이체와 입력 정정 대체를 동시에 지정할 수 없습니다.')
    payments=records(conn,None,PAYMENT)
    matches=[checked(r) for r in payments if r['data']['bank_key']==key]
    replaced=None
    if data['replaces_payment_id']:
        replaced=get(conn,org,data['replaces_payment_id'],PAYMENT)
        if data['status']!='SUCCESS' or replaced['data']['bank_key']!=key or not voided(conn,org,replaced['id']) or money(replaced['data']['bank_amount'])!=amount:
            s.reject('SORT_E08','replaces_payment_id','입력 정정 대체는 같은 실제 은행 거래·금액의 무효 기록에만 연결하세요.')
    if matches and (not replaced or any(r['id']!=replaced['id'] and not voided(conn,r['org'],r['id']) for r in matches)):
        s.reject('SORT_E05','transaction_id','같은 은행 계좌참조·일자·거래번호가 이미 기록되어 있습니다.')
    if replaced and any(r['data'].get('replaces_payment_id')==data['replaces_payment_id'] for r in payments):s.reject('SORT_E05','replacement','이미 대체된 지급 기록입니다.')
    predecessor=get(conn,org,data['retry_of'],PAYMENT) if data['retry_of'] else None
    if predecessor and (predecessor['data']['status'] not in {'FAILED','CANCELLED'} or predecessor['data']['bank_key'][0]!=key[0]):s.reject('SORT_E09','retry_of','같은 계좌참조의 실패/취소 원거래를 선택하세요.')
    if predecessor and data['paid_date']<predecessor['data']['paid_date']:s.reject('SORT_E09','paid_date','재이체일이 실패 원거래일보다 빠릅니다.',422)
    identities=set();result=[];total=Decimal(0)
    for row in data['lines']:
        identity=(row['case_id'],row['member_id'])
        if identity in identities:s.reject('SORT_E09','lines','같은 상신본·회원의 지급 배부를 합쳐 입력하세요.')
        identities.add(identity);value=money(row['amount'])
        if value<=0:s.reject('SORT_E03','amount','지급 배부액은 양수여야 합니다.')
        case=get(conn,org,row['case_id'],CASE)
        if closed(conn,org,case['id']):s.reject('SORT_E08','case','취하된 상신본에 지급을 등록할 수 없습니다.')
        latest=decisions(conn,org,case['id'])
        if not latest or latest[-1]['id']!=row['decision_id']:s.reject('SORT_E08','decision_id','최신 서면 결재 기록을 선택하세요.')
        decision=latest[-1];member=next((r for r in decision['data']['members'] if r['member_id']==row['member_id']),None)
        stored_proof(conn,org,decision['data']['proof'])
        if data['paid_date']<decision['data']['decision_date'] and not data['source_change_note'].strip():s.reject('SORT_E08','source_change_note','결재일 전 이체입니다. 선지급 사실과 외부 확인 근거를 명시하세요.')
        if not member or member['result']!='APPROVED':s.reject('SORT_E05','member','가결로 기록된 수혜 회원이 아닙니다.')
        recorded,_,last_refund=balances(conn,org,case['id']);balance=recorded.get(row['member_id'],dict(paid=Decimal(0),refund=Decimal(0)))
        if data['status']=='SUCCESS' and last_refund and decision['id']<last_refund:s.reject('SORT_E08','decision_id','환수 후 추가 지급은 새로운 서면 결재 결과를 기록해야 합니다.')
        if value>money(member['amount'])-balance['paid']+balance['refund']:s.reject('SORT_E05','amount','기록된 서면 결재액의 미지급 잔액을 초과합니다.')
        if current_hash(conn,org,case)!=decision['data']['source_hash'] and not data['source_change_note'].strip():s.reject('SORT_E08','source_change_note','원본 변경이 있습니다. 실제 송금 사실과 재검토 근거를 명시하세요.')
        if predecessor:
            original=next((r for r in predecessor['data']['lines'] if (r['case_id'],r['member_id'])==identity),None)
            used=sum((money(l['amount']) for p in payments if p['data'].get('retry_of')==predecessor['id'] and p['data']['status']=='SUCCESS' and not voided(conn,p['org'],p['id']) for l in p['data']['lines'] if (l['case_id'],l['member_id'])==identity),Decimal(0))
            if not original or used+value>money(original['amount']):s.reject('SORT_E05','retry_of','실패 원거래의 해당 회원 재이체 잔액을 초과합니다.')
        result.append(dict(row,amount=str(value),member_name=member['member_name'],member_number=member['member_number']));total+=value
    if total!=amount:s.reject('SORT_E05','bank_amount','은행 거래 금액과 신청·회원별 배부액 합계가 다릅니다.')
    return append(conn,org,PAYMENT,data,dict(bank_key=key,account_reference=data['account_reference'],transaction_id=data['transaction_id'],paid_date=data['paid_date'],bank_amount=str(amount),status=data['status'],
        retry_of=data['retry_of'],replaces_payment_id=data['replaces_payment_id'],proof=evidence,lines=result,source_change_note=data['source_change_note']),actor)


def adjustment(conn,org,payment_id,data,actor):
    original=get(conn,org,payment_id,PAYMENT);request=dict(data,payment_id=payment_id);old=retry(conn,org,ADJUSTMENT,request)
    if old:return dict(id=old['id'])
    if original['data']['status']!='SUCCESS' or voided(conn,org,payment_id):s.reject('SORT_E08','payment','현재 유효한 성공 지급 기록에만 정정·환수를 연결하세요.')
    evidence=proof(conn,org,data['evidence_id']);dated(data['date'])
    if data['date']<original['data']['paid_date']:s.reject('SORT_E09','date','정정·환수일이 원 지급일보다 빠릅니다.',422)
    old_adjustments=adjustments(conn,org,payment_id)
    if data['action']=='VOID_REFUND':
        target=get(conn,org,data['target_adjustment_id'],ADJUSTMENT) if data['target_adjustment_id'] else None
        if not target or target['data']['action']!='REFUND' or target['data']['payment_id']!=payment_id or target['id'] in voided_refunds(conn,org,payment_id):s.reject('SORT_E08','target_adjustment_id','현재 유효한 원 환수 기록을 선택하세요.')
        if data['date']<target['data']['date']:s.reject('SORT_E09','date','환수 입력 정정일이 원 환수일보다 빠릅니다.',422)
        for line in target['data']['lines']:
            decision=decisions(conn,org,line['case_id'])[-1]
            approved=next(r for r in decision['data']['members'] if r['member_id']==line['member_id'])
            current,_,_=balances(conn,org,line['case_id']);balance=current[line['member_id']]
            if approved['result']!='APPROVED' or balance['paid']-balance['refund']+money(line['amount'])>money(approved['amount']):s.reject('SORT_E05','decision','환수 입력 정정 후 순지급액을 포괄하는 외부 결재 근거를 먼저 등록하세요.')
        lines=target['data']['lines'];amount=money(target['data']['amount']);key=None
    elif data['action']=='VOID':
        if old_adjustments:s.reject('SORT_E08','payment','환수 등이 연결된 기록은 전체 입력 무효로 되돌릴 수 없습니다.')
        lines=original['data']['lines'];amount=money(original['data']['bank_amount']);key=None
    else:
        key=bank_key(data['account_reference'],data['date'],data['transaction_id'])
        matches=[r for r in records(conn,None,ADJUSTMENT) if r['data'].get('bank_key')==key and r['data']['action']=='REFUND']
        replacement=get(conn,org,data['replaces_adjustment_id'],ADJUSTMENT) if data['replaces_adjustment_id'] else None
        if replacement and (replacement['data']['payment_id']!=payment_id or replacement['data']['bank_key']!=key or replacement['id'] not in voided_refunds(conn,org,payment_id)):s.reject('SORT_E08','replaces_adjustment_id','같은 환수 거래의 입력 무효 기록을 대체 대상으로 선택하세요.')
        if matches and (not replacement or any(r['id'] not in voided_refunds(conn,r['org'],r['data']['payment_id']) for r in matches)):s.reject('SORT_E05','transaction_id','이미 기록된 환수 은행 거래입니다. 입력 정정은 원 환수를 명시적으로 무효 처리한 뒤 대체하세요.')
        if replacement and any(r['data'].get('replaces_adjustment_id')==replacement['id'] for r in records(conn,org,ADJUSTMENT)):s.reject('SORT_E05','replacement','이미 대체한 환수 기록입니다.')
        lines=[];amount=Decimal(0);identities=set()
        for row in data['lines']:
            identity=(row['case_id'],row['member_id']);value=money(row['amount'])
            if identity in identities:s.reject('SORT_E09','lines','환수 배부행이 중복됩니다.')
            identities.add(identity)
            source=next((r for r in original['data']['lines'] if (r['case_id'],r['member_id'])==identity),None)
            used=sum((money(r['amount']) for a in old_adjustments if a['data']['action']=='REFUND' and a['id'] not in voided_refunds(conn,org,payment_id) for r in a['data']['lines'] if (r['case_id'],r['member_id'])==identity),Decimal(0))
            if not source or value<=0 or used+value>money(source['amount']):s.reject('SORT_E05','amount','원 지급의 해당 회원 환수 가능액을 초과합니다.')
            lines.append(dict(row,amount=str(value)));amount+=value
        if not lines or data['bank_amount'] is None or money(data['bank_amount'])!=amount:s.reject('SORT_E05','bank_amount','환수 거래 금액과 배부 합계를 확인하세요.')
    return append(conn,org,ADJUSTMENT,request,dict(payment_id=payment_id,action=data['action'],date=data['date'],proof=evidence,bank_key=key,transaction_id=data['transaction_id'],amount=str(amount),lines=lines,
        target_adjustment_id=data['target_adjustment_id'],replaces_adjustment_id=data['replaces_adjustment_id']),actor)


def close(conn,org,case_id,data,actor):
    case=get(conn,org,case_id,CASE);request=dict(data,case_id=case_id);old=retry(conn,org,CLOSE,request)
    if old:return dict(id=old['id'])
    if closed(conn,org,case_id):s.reject('SORT_E08','case','이미 취하된 상신본입니다.')
    if balances(conn,org,case_id)[1]:s.reject('SORT_E08','case','성공 지급이 한 번이라도 기록된 건은 일반 취하로 물량을 해제할 수 없습니다. 정정·환수 이력을 유지하세요.')
    evidence=proof(conn,org,data['evidence_id'])
    return append(conn,org,CLOSE,request,dict(case_id=case['id'],date=dated(data['date']),proof=evidence),actor)


def view(conn,org,case_id):
    case=get(conn,org,case_id,CASE);history=decisions(conn,org,case_id);decision=history[-1] if history else None
    recorded,ever_paid,last_refund=balances(conn,org,case_id);members=[]
    for proposal in case['data']['proposals']:
        member_id=proposal['member_id'];approved=next((r for r in decision['data']['members'] if r['member_id']==member_id),None) if decision else None
        amount=money(approved['amount']) if approved and approved['result']=='APPROVED' else None
        amounts=recorded.get(member_id,dict(paid=Decimal(0),refund=Decimal(0)));net=amounts['paid']-amounts['refund']
        members.append(dict(proposal,external_result=approved['result'] if approved else None,external_approved_amount=None if amount is None else str(amount),
            recorded_paid_amount=str(amounts['paid']),recorded_refund_amount=str(amounts['refund']),recorded_net_paid=str(net),remaining_to_decision=None if amount is None else str(amount-net)))
    payments=[dict(id=r['id'],**{k:r['data'][k] for k in ('status','transaction_id','paid_date','bank_amount','proof','lines','retry_of','replaces_payment_id')},voided=voided(conn,org,r['id'])) for r in records(conn,org,PAYMENT) if any(l['case_id']==case_id for l in r['data']['lines'])]
    for row in payments:row['adjustments']=[dict(id=a['id'],action=a['data']['action'],date=a['data']['date'],amount=a['data']['amount'],proof=a['data']['proof'],entry_voided=a['id'] in voided_refunds(conn,org,row['id'])) for a in adjustments(conn,org,row['id'])]
    current=current_hash(conn,org,case)
    proof_issues=[]
    relevant=[*history,*[r for r in records(conn,org,PAYMENT) if any(l['case_id']==case_id for l in r['data']['lines'])],*[r for r in records(conn,org,CLOSE) if r['data']['case_id']==case_id]]
    for payment in payments:relevant.extend(adjustments(conn,org,payment['id']))
    for record in relevant:
        try:stored_proof(conn,org,record['data']['proof'])
        except s.SortingError as exc:proof_issues.append(f'기록 {record["id"]}: {exc.message}')
    return dict(id=case_id,claim_id=case['data']['claim_id'],document_number=case['data']['document_number'],submitted_date=case['data']['submitted_date'],policy_basis=case['data']['policy_basis'],snapshot=case['data']['snapshot'],
        members=members,decisions=[dict(id=r['id'],**r['data']) for r in history],latest_decision_id=decision['id'] if decision else None,payments=payments,closed=closed(conn,org,case_id),ever_paid=ever_paid,
        closures=[dict(id=r['id'],date=r['data']['date'],reason=r['data']['reason'],proof=r['data']['proof']) for r in records(conn,org,CLOSE) if r['data']['case_id']==case_id],
        source_changed=current!=case['data']['source_hash'],decision_source_changed=decision is not None and current!=decision['data']['source_hash'],proof_issues=proof_issues,system_approval=False,bank_verified=False,
        label='서면 결재·외부 지급 결과 기록 / 전자승인·자동송금·은행 검증 아님')


def listing(conn,org,claim_id):
    s.scoped(conn,s.claims,org,claim_id)
    return [dict(id=r['id'],document_number=r['data']['document_number'],date=r['data']['submitted_date'],closed=closed(conn,org,r['id'])) for r in records(conn,org,CASE) if r['data']['claim_id']==claim_id]


def report_body(conn,org,case_id,kind,member_id=None):
    state=view(conn,org,case_id)
    if kind!='SUBMISSION' and not state['latest_decision_id']:s.reject('SORT_E08','decision','서면 결재 결과를 먼저 등록하세요.')
    if kind=='INSTRUCTION' and state['closed']:s.reject('SORT_E08','case','취하된 상신본의 지급자료를 새로 출력할 수 없습니다.')
    state['members']=[r for r in state['members'] if member_id is None or r['member_id']==member_id]
    if not state['members']:s.reject('SORT_E09','member','이 상신본의 회원이 아닙니다.',404)
    own_lines={r['line_id'] for r in state['snapshot']['rows'] if member_id is None or r['member_id']==member_id}
    state['snapshot']=dict(state['snapshot'],rows=[r for r in state['snapshot']['rows'] if member_id is None or r['member_id']==member_id],
        review_issues=[r for r in state['snapshot']['review_issues'] if r.get('allocation_id') is None or r['allocation_id'] in own_lines])
    # Transfer totals may include other cases/members; a personal copy exposes only its allocations.
    safe=[]
    for p in state['payments']:
        lines=[r for r in p['lines'] if r['case_id']==case_id and (member_id is None or r['member_id']==member_id)]
        if lines:
            corrections=[]
            for a in adjustments(conn,org,p['id']):
                own=[r for r in a['data']['lines'] if r['case_id']==case_id and (member_id is None or r['member_id']==member_id)]
                if own:corrections.append(dict(id=a['id'],action=a['data']['action'],date=a['data']['date'],proof=a['data']['proof'],amount=str(sum((money(r['amount']) for r in own),Decimal(0))),entry_voided=a['id'] in voided_refunds(conn,org,p['id'])))
            safe.append(dict(id=p['id'],transaction_id=p['transaction_id'],paid_date=p['paid_date'],status=p['status'],voided=p['voided'],lines=lines,proof=p['proof'],adjustments=corrections))
    state['payments']=safe
    if member_id is not None:
        state['snapshot'].pop('remaining',None);state['snapshot'].pop('costs',None)
    if kind=='SUBMISSION':
        state['latest_decision_id']=None;state['payments']=[]
        state['members']=[dict(r,external_result=None,external_approved_amount=None,recorded_paid_amount=None,recorded_refund_amount=None,recorded_net_paid=None,remaining_to_decision=None) for r in state['members']]
    latest=state['decisions'][-1] if state['decisions'] and kind!='SUBMISSION' else None
    state['external_decision']=None if latest is None else {k:latest[k] for k in ('id','document_number','decision_date','proof','decision_basis','change_note','entry_correction')}
    state.pop('decisions',None)
    return state


HEADERS=['회원','사업자번호','귀속량 kg','계산 참고액 원 (미심사)','상신 제안액 원','서면 결재 결과','서면 결재액 원','지급 기록액 원','환수 기록액 원','순지급 기록액 원','미지급 잔액 원']
TITLES={'SUBMISSION':'서면 결재 상신자료','INSTRUCTION':'서면 결재 기준 지급자료','RESULT':'외부 지급 결과 명세'}
RESULT_LABELS={'APPROVED':'서면 가결','HOLD':'서면 보류','REJECTED':'서면 부결'}
BANK_LABELS={'SUCCESS':'이체 성공 기록','FAILED':'이체 실패 기록','CANCELLED':'은행 취소 기록'}
ADJUSTMENT_LABELS={'VOID':'지급 입력 무효','REFUND':'환수 입금','VOID_REFUND':'환수 입력 무효'}


def values(state,row):
    original=[r for r in state['snapshot']['rows'] if r['member_id']==row['member_id']]
    reference=beneficiaries.totals(original,'all')[0][3]
    return [row['member_name'],row['member_number'],row['quantity'],reference,row['prepared_amount'],RESULT_LABELS.get(row['external_result']),row['external_approved_amount'],row['recorded_paid_amount'],row['recorded_refund_amount'],row['recorded_net_paid'],row['remaining_to_decision']]


def workbook(state,kind):
    from openpyxl import Workbook
    from epms.sorting_books import workbook_bytes
    book=Workbook();sheet=book.active;sheet.title=TITLES[kind]
    sheet.append([state['label']]);sheet.append(['상신 문서',state['document_number'],'상신본 ID',str(state['id']),'최신 결재 기록 ID',str(state['latest_decision_id'] or '')])
    sheet.append(['적용 기준·쟁점',state['policy_basis']]);sheet.append(['상신 후 원본 변경',str(state['source_changed']),'결재 후 원본 변경',str(state['decision_source_changed'])])
    if state['external_decision']:
        decision=state['external_decision'];sheet.append(['결재 문서번호',decision['document_number'],'결재일',decision['decision_date'],'결재 증빙 ID',str(decision['proof']['id'])]);sheet.append(['결재 근거',decision['decision_basis'],'원본 변경 검토',decision['change_note']])
    sheet.append(['서명/결재란','담당','검토','결재'])
    sheet.append(HEADERS)
    for row in state['members']:sheet.append(values(state,row))
    detail=book.create_sheet('신고·선별 귀속 근거');detail.append(beneficiaries.HEADERS)
    for row in state['snapshot']['rows']:detail.append([None if v is None else str(v) for v in beneficiaries.cells(row)])
    issues=book.create_sheet('보완·정책 쟁점');issues.append(['시스템이 정책·자격·은행을 승인한 문서가 아닙니다. 외부 결재 및 증빙에 근거해 기록합니다.'])
    for row in state['snapshot']['review_issues']:issues.append([row['message']])
    for issue in state['proof_issues']:issues.append(['외부 증빙 재확인',issue])
    bank=book.create_sheet('외부 거래 기록');bank.append(['거래 기록 ID','거래번호','일자','상태','입력 무효','회원','이 명세 배부액 원'])
    for payment in state['payments']:
        for row in payment['lines']:bank.append([str(payment['id']),payment['transaction_id'],payment['paid_date'],BANK_LABELS[payment['status']],'예' if payment['voided'] else '아니오',row['member_name'],row['amount'],'이체 증빙 ID',str(payment['proof']['id'])])
        for a in payment['adjustments']:bank.append([str(a['id']),'원 지급 '+str(payment['id']),a['date'],ADJUSTMENT_LABELS[a['action']],'예' if a['entry_voided'] else '아니오',None,a['amount'],'증빙 ID',str(a['proof']['id'])])
    return workbook_bytes(book)


def printable(state,kind):
    def text(value):return escape('미확인' if value is None else str(value),quote=True)
    def currency(value):return None if value is None else format(Decimal(value),',f')
    rows=''
    for row in state['members']:
        data=values(state,row)
        rows+='<tr>'+''.join(f'<td>{text(currency(v) if i in {3,4,6,7,8,9,10} else v)}</td>' for i,v in enumerate(data))+'</tr>'
    header=''.join(f'<th>{text(v)}</th>' for v in HEADERS)
    detail=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in [r['declaration_number'],r['declaration_line'],r['facility_name'],r['work_date'],r['member_name'],r['quantity'],r['evidence_id'],r['source_reference']])+'</tr>' for r in state['snapshot']['rows'])
    issues=''.join(f'<li>{text(r["message"])}</li>' for r in state['snapshot']['review_issues'])
    issues+=''.join(f'<li>{text(message)}</li>' for message in state['proof_issues'])
    decision=state['external_decision']
    decision_text='' if decision is None else f'<p>외부 결재 문서 {text(decision["document_number"])} · {text(decision["decision_date"])} · 증빙 {decision["proof"]["id"]}</p><p>결재 근거: {text(decision["decision_basis"])} · 변경 검토: {text(decision["change_note"])}</p>'
    bank_rows=''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in [p['id'],p['transaction_id'],p['paid_date'],BANK_LABELS[p['status']],'예' if p['voided'] else '아니오',r['member_name'],currency(r['amount']),p['proof']['id']])+'</tr>' for p in state['payments'] for r in p['lines'])
    refund_rows=''.join(f'<p>원 지급 {p["id"]} · {ADJUSTMENT_LABELS[a["action"]]} 기록 {a["id"]}{" (입력 무효 이력)" if a["entry_voided"] else ""} · {text(a["date"])} · {text(currency(a["amount"]))}원 · 증빙 {a["proof"]["id"]}</p>' for p in state['payments'] for a in p['adjustments'])
    bank_table=f'<h2>외부 거래 기록</h2><table><thead><tr><th>기록 ID</th><th>거래번호</th><th>일자</th><th>상태</th><th>입력 무효</th><th>회원</th><th>이 명세 배부액 원</th><th>증빙 ID</th></tr></thead><tbody>{bank_rows}</tbody></table>{refund_rows}'
    warning='<p><strong>원본 변경 있음: 외부 재검토/재결재 근거를 확인하세요. 원 상신본의 물량·금액을 자동 변경하지 않습니다.</strong></p>' if state['source_changed'] or state['decision_source_changed'] else ''
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>{TITLES[kind]}</title>
<style>body{{font:12px sans-serif}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}td,th{{border:1px solid #888;padding:5px;overflow-wrap:anywhere}}@page{{size:A4 landscape;margin:10mm}}thead{{display:table-header-group}}tr{{break-inside:avoid}}@media print{{button{{display:none}}}}</style>
<h1>{TITLES[kind]}</h1><p>{text(state['label'])}</p><p>상신 문서 {text(state['document_number'])} · 상신본 {state['id']} · 결재 기록 {text(state['latest_decision_id'])}</p>
<p>적용 기준·쟁점: {text(state['policy_basis'])}</p>{decision_text}{warning}<button onclick="window.print()">인쇄/PDF 저장</button>
<table><tr><th>담당</th><td style="height:45px"></td><th>검토</th><td></td><th>결재</th><td></td></tr></table>
<h2>회원별 금액</h2><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>
<h2>신고·선별 귀속 근거</h2><table><thead><tr><th>신고번호</th><th>품목란</th><th>선별장</th><th>선별일</th><th>회원</th><th>귀속량 kg</th><th>증빙 ID</th><th>원물/LOT</th></tr></thead><tbody>{detail}</tbody></table>
{bank_table}<h2>보완·정책 쟁점</h2><ul>{issues}</ul><p>원본·정책 및 외부 결재/은행 증빙의 진위는 기관 절차로 확인합니다. 문서 재출력은 새로운 승인·송금이 아닙니다.</p></html>'''
