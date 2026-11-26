"""Read-only source, transfer and allocation reconciliation; not subsidy payment."""
import hashlib,re
from datetime import date
from decimal import Decimal,localcontext
from epms import sorting as s,sorting_finance as finance


def review(conn,org,start,end,facility_id=None):
    if not all(re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',v) for v in (start,end)) or start>end:
        s.reject('SORT_E05','period','유효한 귀속월 YYYY-MM 범위를 입력하세요.',422)
    try:
        date.fromisoformat(start+'-01');date.fromisoformat(end+'-01')
    except ValueError:
        s.reject('SORT_E05','period','유효한 귀속월 YYYY-MM 범위를 입력하세요.',422)
    if facility_id is not None:
        s.scoped(conn,s.facilities,org,facility_id)
    expenses=[dict(r) for r in conn.execute(s.expenses.select().where(s.expenses.c.org==org).order_by(s.expenses.c.id)).mappings()]
    payments=[dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='PAYMENT_HISTORY').order_by(s.records.c.id)).mappings()]
    allocations=s.list_rows(conn,s.expense_allocations,org)
    claims={r['id']:r for r in s.list_rows(conn,s.claims,org)}
    proof_cache={}
    rows=[];totals={k:Decimal(0) for k in ('incurred','success','candidate','allocated','available','overallocated')}
    def proof(entity):
        if entity not in proof_cache:
            file=conn.execute(s.evidence.select().where(s.evidence.c.org==org,s.evidence.c.id==entity)).mappings().first()
            proof_cache[entity]=dict(id=entity,integrity='MISSING' if file is None else 'OK' if hashlib.sha256(file['content']).hexdigest()==file['sha'] else 'CHANGED',sha=None if file is None else file['sha'])
        return proof_cache[entity]
    with localcontext() as context:
        context.prec=60
        for expense in expenses:
            data=expense['data']
            if not start<=data['month']<=end or (facility_id is not None and expense['facility']!=facility_id):
                continue
            transfer_rows=[];by_status={k:Decimal(0) for k in ('REQUESTED','SUCCESS','FAILED','CANCELLED')};issues=[]
            selected=[p for p in payments if p['data']['expense_id']==expense['id']]
            index={p['id']:p for p in selected}
            proofs=[proof(data['evidence_id'])]
            if data.get('separation_evidence_id'):
                proofs.append(proof(data['separation_evidence_id']))
            original={k:v for k,v in data.items() if k not in {'input_hash','eligibility','reason'}}
            if data.get('input_hash')!=s.digest(original):
                issues.append('비용 등록 당시 입력 해시와 현재 기록이 다릅니다.')
            for p in selected:
                value=p['data'];by_status[value['status']]+=s.decimal(value['amount'])
                source=proof(value['evidence_id']);proofs.append(source)
                retry=value.get('retry_of');predecessor=index.get(retry)
                if retry and (predecessor is None or predecessor['data']['status'] not in {'FAILED','CANCELLED'}):
                    issues.append('재이체 원거래 연결을 확인하세요.')
                transfer_rows.append(dict(id=p['id'],transaction_id=value['transaction_id'],amount=value['amount'],date=value['date'],status=value['status'],retry_of=retry,evidence=source))
            eligibility,reason=finance.eligibility(data)
            incurred=s.decimal(data['amount'])
            candidate=min(incurred,by_status['SUCCESS']) if eligibility=='REFERENCE_CANDIDATE' else Decimal(0)
            assigned=[];used=Decimal(0)
            for a in allocations:
                if a['expense']!=expense['id']:
                    continue
                claim=claims.get(a['claim'])
                if a['active']:
                    used+=s.decimal(a['amount'])
                assigned.append(dict(id=a['id'],claim_id=a['claim'],amount=a['amount'],active=bool(a['active']),claim_state=None if claim is None else claim['state']))
                if claim is None or a['active'] and claim['state'] in {'WITHDRAWN','REJECTED'}:
                    issues.append('비용 배부와 신청 상태가 일치하지 않습니다.')
            if any(p['integrity']!='OK' for p in proofs):
                issues.append('원본 증빙 누락 또는 해시 변경: 정상 이체 기록만으로 적격 확인 불가')
            if by_status['SUCCESS']>incurred:
                issues.append('정상 이체 합계가 발생액을 초과합니다.')
            remaining=candidate-used
            if remaining<0:
                issues.append('활성 배부액이 검토 후보 비용을 초과합니다.')
            values=dict(incurred=incurred,success=by_status['SUCCESS'],candidate=candidate,allocated=used,available=max(Decimal(0),remaining),overallocated=max(Decimal(0),-remaining))
            for key,amount in values.items():
                totals[key]+=amount
            rows.append(dict(id=expense['id'],facility_id=expense['facility'],month=data['month'],external_id=data['external_id'],category=data['category'],eligibility=eligibility,reason=reason,
                **{k:str(v) for k,v in values.items()},balance=str(remaining),payment_totals={k:str(v) for k,v in by_status.items()},payments=transfer_rows,allocations=assigned,evidence=proofs,issues=issues,
                record_hash=s.digest(data),payment_verified=False,payable_amount=None))
            rows[-1]['evidence']=list({p['id']:p for p in proofs}.values())
    return dict(label='과거 비용·이체 기록 대사 / 신규 보조금 지급 아님',unit='KRW',filters=dict(start=start,end=end,facility_id=facility_id),rows=rows,
        totals={k:str(v) for k,v in totals.items()},basis='비용 귀속월 기준, 연결 이체는 일자와 관계없이 모두 표시; SUCCESS만 합산하며 실패/취소와 재이체를 중복 차감하지 않음',
        limitation='현재 기록의 검토 후보액과 활성 배부 기준; 은행 원장 진위·정책 적격·실제 지급 검증 아님. 증빙 무결성 문제는 별도 표시하며 기록을 자동 수정하지 않음',
        operational_approval=False,payable_amount=None)
