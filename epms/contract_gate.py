"""Agreement prerequisite for confirmation; does not change export amount attribution."""
from epms import member_contracts as mc, rules


def context(conn, org):
    return [dict(r) for r in conn.execute(mc.contracts.select().where(mc.contracts.c.org == org)
            .order_by(mc.contracts.c.id.desc())).mappings()]


def evaluate(row, members, agreements):
    if rules.excluded_line(row):
        return [], []
    def confirmed(role):
        member = members.get(rules.business_number(row.get(role)))
        if not member or row.get(role+'_state') in rules.NON_MEMBER_STATES | {'원본에 없음'}:
            return None
        names = [member['name'], *rules.clean(member.get('aliases')).split('|')]
        if rules.company_name(row.get(role+'_name')) not in {rules.company_name(n) for n in names if rules.clean(n)}:
            return None
        return member
    producer = confirmed('maker')
    if not producer or producer.get('kind') not in {'생산자','복수'}:
        return [], []  # Existing manufacturer validation already blocks this case.
    exporters = [m for role in ('owner','agent') if (m := confirmed(role))]
    # A producer acting as exporter needs no agreement with itself.
    if any(m['id'] == producer['id'] or rules.business_number(m['number']) == rules.business_number(producer['number']) for m in exporters):
        return [], [dict(producer_id=producer['id'], self_export=True)]
    reference = rules.day(row.get('shipped')) or rules.day(row.get('declared'))
    if not reference:
        return [rules.issue('D092','shipped','계약 유효기간을 확인할 선적일 또는 신고일을 입력하고 계약서 등록을 확인하세요.')], []
    day = reference.isoformat()
    for agreement in agreements:
        if agreement['producer_id'] != producer['id']:
            continue
        if not any(m['id'] == agreement['exporter_id'] for m in exporters):
            continue
        if mc.assess(agreement, day)['state'] == '유효':
            return [], [dict(producer_id=producer['id'], exporter_id=agreement['exporter_id'],
                contract_id=agreement['id'], revision=agreement['revision'], reference=day)]
    names = ' / '.join(dict.fromkeys(m['name'] for m in exporters)) or '수출화주·수출대행사 회원사'
    message = (f"확정 차단: 제조자 {producer['name']}와 {names} 중 한 업체와의 {day} 기준 유효한 계약을 찾을 수 없습니다. "
               '계약서 등록에서 당사자·계약기간·해지 여부를 확인하세요. (선적일 우선, 없으면 신고일 기준)')
    return [rules.issue('D090','maker',message)], []


def validation(conn, org, rows, members, policy):
    index = {rules.business_number(m['number']):m for m in members}
    agreements = context(conn, org)
    checks, evidence = [], []
    for row in rows:
        reference = rules.day(row.get(policy['basis']))
        if reference and reference.year != policy['year']:
            continue
        issues, links = evaluate(row, index, agreements)
        checks.extend(dict(p,row_id=row['id']) for p in issues)
        evidence.extend(dict(link,row_id=row['id']) for link in links)
    return dict(checks=checks,evidence=evidence)


def calculate(conn, org, rows, members, policy):
    result = rules.calculate(rows,members,policy)
    gate = validation(conn,org,rows,members,policy)
    result['checks'] += gate['checks']
    result['ready'] = result['ready'] and not gate['checks']
    result['contract_validation'] = gate
    return result
