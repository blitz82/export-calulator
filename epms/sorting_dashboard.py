"""Period-based local review aggregates, with explicit denominators and provenance."""
from datetime import date
from decimal import Decimal,localcontext
from epms import sorting as s


def iso(value):
    try:
        if date.fromisoformat(value).isoformat()!=value:
            raise ValueError()
    except (ValueError,TypeError):
        s.reject('SORT_E09','period','YYYY-MM-DD 형식의 유효한 날짜가 필요합니다.',422)
    return value


def dashboard(conn,org,start,end,as_of,facility_id=None):
    start,end,as_of=iso(start),iso(end),iso(as_of)
    if start>end:
        s.reject('SORT_E09','period','조회 시작일이 종료일보다 늦습니다.',422)
    if facility_id is not None:
        s.scoped(conn,s.facilities,org,facility_id)
    rows=[dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,
        s.records.c.kind.in_(['INTAKE','LOT_TRANSFORM','CONTRACT','PRODUCER_SETTLEMENT'])).order_by(s.records.c.id)).mappings()]
    contracts={r['id']:r for r in rows if r['kind']=='CONTRACT'}
    superseded={r['data'].get('previous_id') for r in rows}
    totals={name:0 for name in ('intake','input','export','domestic','reject','loss','inventory')}
    counts=dict(intake=0,work=0,settlement=0,overdue=0,disputed=0,zero_balance=0)
    money={name:Decimal(0) for name in ('net','paid','remaining','overpaid')}
    states={}
    sources=[]
    issues=[]
    with localcontext() as context:
        context.prec=60
        for row in rows:
            value=row['data'];kind=row['kind']
            if row['id'] in superseded or kind=='CONTRACT':
                continue
            if kind=='PRODUCER_SETTLEMENT':
                contract=contracts.get(value.get('contract_id'))
                if not contract:
                    issues.append(dict(record_id=row['id'],code='MISSING_CONTRACT',message='연결 계약 누락: 정산 집계 제외'))
                    continue
                facility=contract['data']['facility_id'];day=value['due_date']
            else:
                facility=value['facility_id'];day=value['date']
            if (facility_id is not None and facility!=facility_id) or not start<=day<=end:
                continue
            source=dict(record_id=row['id'],kind=kind,revision=row['revision'],facility_id=facility,date=day,
                evidence_id=value['evidence_id'],record_hash=s.digest(dict(id=row['id'],revision=row['revision'],data=value)))
            if kind=='INTAKE':
                quantity=s.kg(value['quantity']);totals['intake']+=quantity;counts['intake']+=1
                source.update(quantity=s.text_quantity(quantity),unit='kg')
            elif kind=='LOT_TRANSFORM':
                quantity=sum(s.kg(p['quantity']) for p in value['inputs'])
                totals['input']+=quantity;counts['work']+=1
                outputs={name:s.kg(value[field]) for name,field in dict(export='export_quantity',domestic='domestic_quantity',
                    reject='reject_quantity',loss='loss_quantity',inventory='inventory_quantity').items()}
                for name,amount in outputs.items():
                    totals[name]+=amount
                if sum(outputs.values())!=quantity:
                    issues.append(dict(record_id=row['id'],code='BALANCE_CONFLICT',message='등록 작업의 물량수지가 다름'))
                source.update(quantity=s.text_quantity(quantity),unit='kg',input_count=len(value['inputs']),
                    export_quantity=s.text_quantity(outputs['export']))
            else:
                net=Decimal(value['net_amount']);paid=s.decimal(value['paid'])
                if not net.is_finite() or net<0:
                    s.reject('SORT_E08','net_amount','저장된 실정산금의 무결성을 확인하세요.')
                remaining=max(Decimal(0),net-paid);overpaid=max(Decimal(0),paid-net)
                overdue=remaining>0 and day<as_of
                for name,amount in dict(net=net,paid=paid,remaining=remaining,overpaid=overpaid).items():
                    money[name]+=amount
                counts['settlement']+=1;counts['overdue']+=int(overdue)
                counts['disputed']+=int(value['state']=='DISPUTED');counts['zero_balance']+=int(remaining==0)
                states[value['state']]=states.get(value['state'],0)+1
                source.update(quantity=str(remaining),unit='KRW',state=value['state'],net=str(net),paid=str(paid),
                    overpaid=str(overpaid),overdue=overdue,contract_id=contract['id'],contract_revision=contract['revision'],
                    contract_hash=s.digest(contract['data']),contract_evidence_id=contract['data']['evidence_id'])
            sources.append(source)
        ratio=None if totals['input']==0 else str(Decimal(totals['export'])/Decimal(totals['input']))
    return dict(label='등록 자료 검토 통계 / 공식 KPI 아님',filters=dict(start=start,end=end,as_of=as_of,facility_id=facility_id),
        version_basis='현재 최종 개정본 기준; 과거 시점의 원장 복원이나 실제 지급 검증 아님',
        production=dict(unit='kg',**{key:s.text_quantity(amount) for key,amount in totals.items()},
            export_suitability=dict(numerator=s.text_quantity(totals['export']),denominator=s.text_quantity(totals['input']),ratio=ratio,
                definition='조회기간 작업일에 해당하는 각 선별·재선별 작업의 적합량 / 투입량; 재선별을 각각 포함하며 실수출률 아님'),
            intake_definition='조회기간 입고일에 해당하는 고유 입고 기록량; 작업 투입량과 동일한 원물 집단이 아니므로 비율로 나누지 않음'),
        settlement=dict(unit='KRW',**{key:str(amount) for key,amount in money.items()},states=states,
            definition='지급기한이 조회기간에 속하는 최종 정산 기록; 잔액=max(실정산금-기록 지급액,0), 초과지급 별도',
            overdue_definition='기록 잔액>0 이고 지급기한<판단 기준일; 기한 당일 제외, 분쟁 건도 포함, 자동 탈락 아님'),
        counts=counts,sources=sources,issues=issues,source_set_hash=s.digest(sources),
        contract_fulfillment=None,joint_sorting_rate=None,trade_settlement_rate=None,payable_amount=None,
        missing_basis=['계약별 실제 수출이행 연결','중복 없는 생산 원물 분모와 공통 집단','정산 완료율의 확정 정책·실제 지급 검증'],
        operational_approval=False)
