"""Append-only contracts, settlements, certificates and disposition review records."""
from decimal import Decimal
import re
from epms import sorting as s, store, rules


def append(conn,org,kind,data,actor,previous_id=None):
    allowed={'CONTRACT','PLAN','PRODUCER_SETTLEMENT','CERTIFICATE','SANCTION','EXTERNAL_LEDGER','ADJUSTMENT','BUDGET'}
    if kind not in allowed:
        s.reject('SORT_E09','kind','허용되지 않은 검토 기록입니다.',422)
    evidence=s.scoped(conn,s.evidence,org,data['evidence_id'])
    revision=1
    if previous_id:
        previous=s.scoped(conn,s.records,org,previous_id)
        if previous['kind']!=kind:
            s.reject('SORT_E08','previous_id','개정 대상 기록 종류가 다릅니다.')
        if any(r['data'].get('previous_id')==previous_id for r in s.list_rows(conn,s.records,org)):
            s.reject('SORT_E08','previous_id','이미 개정된 기록입니다.')
        revision=previous['revision']+1
    value=dict(data)
    if kind in {'CONTRACT','PLAN','CERTIFICATE'}:
        facility=s.scoped(conn,s.facilities,org,data['facility_id'])
        s.kg(data['quantity'])
        if not data.get('product'):
            s.reject('SORT_E09','product','품목을 입력하세요.',422)
        if kind!='CERTIFICATE' and data['product']!=facility['data']['product']:
            s.reject('SORT_E09','product','시설과 기록의 품목이 다릅니다.',422)
    if kind=='CONTRACT':
        if not rules.day(data.get('start')) or not rules.day(data.get('end')) or data['start']>data['end']:
            s.reject('SORT_E09','period','계약 유효기간을 확인하세요.',422)
        if not data.get('parties') or not data.get('terms'):
            s.reject('SORT_E09','terms','당사자와 정산 조건을 기록하세요.',422)
        value['equivalence']='POLICY_PENDING'
    if kind=='PLAN':
        if not data.get('period') or not data.get('change_reason'):
            s.reject('SORT_E09','period','계획기간과 등록·변경 사유를 기록하세요.',422)
        if not re.fullmatch(r'[1-9][0-9]{3}(?:-(?:0[1-9]|1[0-2]))?',data['period']):
            s.reject('SORT_E09','period','계획기간은 YYYY 또는 YYYY-MM 형식이어야 합니다.',422)
        value['exceeding_plan_disqualifies']=False
    if kind=='PRODUCER_SETTLEMENT':
        contract=s.scoped(conn,s.records,org,data['contract_id'])
        if contract['kind']!='CONTRACT':
            s.reject('SORT_E09','contract_id','계약을 선택하세요.',422)
        names=('sales','sorting_deduction','other_deduction','fee','adjustment_add','adjustment_subtract','paid')
        amounts={k:s.decimal(data[k],k) for k in names}
        net=amounts['sales']-amounts['sorting_deduction']-amounts['other_deduction']-amounts['fee']+amounts['adjustment_add']-amounts['adjustment_subtract']
        if net<0 or data['state'] not in {'NOT_DUE','UNPAID','DISPUTED','COMPLETED'} or not rules.day(data.get('due_date')):
            s.reject('SORT_W06','state','정산상태·지급기한·실정산금을 확인하세요.',422)
        if data['state']=='COMPLETED' and net!=amounts['paid']:
            s.reject('SORT_W04','paid','완료 상태의 실제 지급액과 실정산금이 다릅니다.')
        value.update(net_amount=str(net),government_subsidy=None,refund=None)
        paid_date=data.get('paid_date')
        payment_evidence=data.get('payment_evidence_id')
        if paid_date and not rules.day(paid_date):
            s.reject('SORT_W06','paid_date','실제 거래지급일의 날짜 형식을 확인하세요.',422)
        if payment_evidence not in (None,''):
            if type(payment_evidence)!=int or payment_evidence<=0:
                s.reject('SORT_E08','payment_evidence_id','거래지급 증빙 ID를 확인하세요.',422)
            proof=s.scoped(conn,s.evidence,org,payment_evidence)
            value['payment_evidence_hash']=proof['sha']
        value.update(payment_date_status='RECORDED_FOR_REVIEW' if paid_date and payment_evidence else 'UNVERIFIED',actual_payment_verified=False)
    if kind=='CERTIFICATE':
        for field in ('issuer','member_number','start','end','duplicate_statement'):
            if not data.get(field):
                s.reject('SORT_E08',field,'사무국 발급 확인서 필수정보를 입력하세요.',422)
        if not rules.day(data['start']) or not rules.day(data['end']) or data['start']>data['end']:
            s.reject('SORT_E08','period','확인서 기간을 확인하세요.',422)
        value['issuance_status']='UNVERIFIED_EXTERNAL_DOCUMENT'
    if kind=='SANCTION':
        if data['state'] not in {'INVESTIGATION','DELIBERATION','FINAL','EXPIRED','CANCELLED'}:
            s.reject('SORT_E09','state','제재 사건 상태를 확인하세요.',422)
        if data['state']=='FINAL' and any(not data.get(k) for k in ('authority','basis','start','end','target','product','program','scope')):
            s.reject('SORT_E07','scope','확정 처분의 권한·근거·효력·대상·범위를 확인하세요.',422)
        if data['state']=='FINAL' and (not rules.day(data['start']) or not rules.day(data['end']) or data['start']>data['end']):
            s.reject('SORT_E07','period','확정 처분의 실제 효력기간을 확인하세요.',422)
        value.update(applied=False,policy_status='POLICY_PENDING',automatic_deduction=None)
    if kind=='EXTERNAL_LEDGER':
        for field in ('program','year','issuer','version','valid_from','valid_to'):
            if not data.get(field):
                s.reject('SORT_W07',field,'외부원장 출처·버전·기간을 기록하세요.',422)
        value['reconciliation_status']='REVIEW_PENDING'
    if kind=='ADJUSTMENT':
        s.scoped(conn,s.claims,org,data['claim_id'])
        value.update(execution='REVIEW_CANDIDATE_ONLY',recovery_executed=False)
    if kind=='BUDGET':
        s.decimal(data['amount'])
        value.update(status='HYPOTHETICAL_REVIEW_ONLY',available_for_payment=None)
    value.update(previous_id=previous_id,actor=actor,time=store.now(),evidence_hash=evidence['sha'])
    entity_id=conn.execute(s.records.insert().values(org=org,kind=kind,revision=revision,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_'+kind.lower(),entity_id,'검토 기록 신규 버전',None,dict(previous_id=previous_id,revision=revision))
    return entity_id


def sanction_candidate(record,*,day,target,product,program,scope,approved_test_policy=False):
    # Only synthetic policy testing can exercise applicability, never live deduction.
    if not approved_test_policy or record['state']!='FINAL':
        return False
    return (record['start']<=day<=record['end'] and all(record[k]==v for k,v in
            dict(target=target,product=product,program=program,scope=scope).items()))
