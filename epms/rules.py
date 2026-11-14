"""Deterministic validation and attribution for one organization."""
import re
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

VERSION = "EPMS-1.2"
ROLES = ("agent", "owner", "maker")
NON_MEMBER = "회원사 아님"
NON_MEMBER_STATES = {NON_MEMBER, "비회원 확인"}


def clean(value):
    return unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()


def company_name(value):
    return re.sub(r"\s+|주식회사|\(주\)", "", clean(value)).casefold()


def business_number(value):
    return re.sub(r"[\s-]", "", clean(value))


def valid_number(value):
    if not re.fullmatch(r"[0-9]{10}", value):
        return False
    digits = list(map(int, value))
    total = sum(a * b for a, b in zip(digits, (1, 3, 7, 1, 3, 7, 1, 3, 5)))
    return (total + digits[8] * 5 // 10 + digits[9]) % 10 == 0


def day(value):
    value = clean(value)
    if re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    elif re.fullmatch(r"[0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2}", value):
        y, m, d = re.split(r"[./-]", value)
        value = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def decimal(value):
    value = clean(value)
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", value):
        return None
    try:
        result = Decimal(value.replace(",", ""))
        return result if len(result.as_tuple().digits) <= 15 else None
    except InvalidOperation:
        return None


def issue(code, field, message, severity="ERROR"):
    return dict(code=code, field=field, message=message, severity=severity)


def validate_member(row, existing):
    issues = []
    name = company_name(row.get("name"))
    number = business_number(row.get("number"))
    if not name:
        issues.append(issue("M001", "name", "회원사명을 입력하세요."))
    if not number:
        issues.append(issue("M002", "number", "사업자등록번호를 입력하세요."))
    elif not re.fullmatch(r"[0-9]{10}", number):
        issues.append(issue("M003", "number", "사업자등록번호는 숫자 10자리입니다."))
    elif not valid_number(number):
        issues.append(issue("M004", "number", "사업자등록번호 검증숫자를 확인하세요."))
    for member in existing:
        if member["id"] == row.get("id"):
            continue
        if number and number == business_number(member.get("number")):
            issues.append(issue("M005", "number", "이 통합조직에 이미 등록된 사업자번호입니다."))
            break
    if row.get("kind") not in ("생산자", "수출업체", "복수"):
        issues.append(issue("M011", "kind", "회원사 유형을 선택하세요."))
    dates = {key: day(row.get(key)) for key in ("applied", "approved", "withdrawn")}
    for key, parsed in dates.items():
        if row.get(key) and not parsed:
            issues.append(issue("M010", key, "날짜를 YYYY-MM-DD 형식으로 입력하세요."))
    if not dates["approved"]:
        issues.append(issue("M017", "approved", "회원 가입승인일을 확인하세요."))
    for early, late, code in (("applied", "approved", "M008"), ("approved", "withdrawn", "M009")):
        if dates[early] and dates[late] and dates[late] < dates[early]:
            issues.append(issue(code, late, "가입 및 탈퇴 날짜의 순서를 확인하세요."))
    if row.get("email") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["email"]):
        issues.append(issue("M012", "email", "이메일 주소를 확인하세요."))
    if row.get("phone") and not re.fullmatch(r"[+0-9() -]{7,24}", row["phone"]):
        issues.append(issue("M013", "phone", "전화번호를 확인하세요."))
    return issues


def validate_line(row, members, policy=None):
    issues = []
    number = clean(row.get("number"))
    if not number:
        issues.append(issue("D001", "number", "수출신고번호를 입력하세요."))
    elif not re.fullmatch(r"[0-9]{5}-?[0-9]{2}-?[0-9]{5}(?:[A-Za-z0-9]|[0-9][A-Za-z])", number):
        issues.append(issue("D002", "number", "수출신고번호의 자리수와 구분 기호를 확인하세요."))
    for key, missing, invalid, label in (("line", "D003", "D004", "품목란 번호"),
                                         ("version", "D014", "D013", "정정 버전")):
        value = clean(row.get(key))
        if not re.fullmatch(r"[0-9]{1,8}", value) or not int(value):
            message = (f"{label}는 1부터 99999999까지 숫자로 입력하세요." if value else
                       f"{label}가 비어 있습니다. 원장에서 신고 정보를 확인하세요.")
            issues.append(issue(invalid if value else missing, key, message))
    dates = {}
    for field, missing in (("declared", "D005"), ("shipped", "D006")):
        dates[field] = day(row.get(field))
        required = policy["basis"] == field if policy else field == "shipped"
        if not dates[field] and (required or clean(row.get(field))):
            issues.append(issue("D007" if row.get(field) else missing, field, "날짜를 확인하세요."))
    if dates["declared"] and dates["shipped"] and dates["shipped"] < dates["declared"]:
        issues.append(issue("D008", "shipped", "선적일이 신고일보다 빠릅니다. 원본을 확인하세요.", "WARNING"))
    for field, missing, invalid, negative, zero in (
        ("usd", "D020", "D021", "D022", "D023"), ("kg", "D025", "D026", "D027", "D027")
    ):
        if field == "kg" and not clean(row.get(field)):
            continue
        value = decimal(row.get(field))
        if value is None:
            issues.append(issue(invalid if clean(row.get(field)) else missing, field, "원본의 숫자와 단위를 확인하세요."))
        elif value <= 0:
            issues.append(issue(negative if value < 0 else zero, field, "0 또는 음수 값은 정정 자료 여부를 확인하세요."))
    hs = re.sub(r"[.\s-]", "", clean(row.get("hs")))
    if not re.fullmatch(r"[0-9]{10}", hs):
        issues.append(issue("D061" if hs else "D060", "hs", "선행 0을 포함한 HS코드 10자리를 입력하세요."))
    elif policy and hs not in policy["hs"]:
        issues.append(issue("D067", "hs", "이 연도의 승인 대상 HS코드 목록을 확인하세요."))
    scope = row.get("scope")
    if scope != "대상":
        issues.append(issue("D065" if scope == "제외" else "D064", "scope",
                            "공동정산 대상 여부를 확인하세요.", "INFO" if scope == "제외" else "ERROR"))
    status = row.get("status")
    if status != "유효":
        issues.append(issue("D010", "status", "신고 유효 상태를 확인하세요.",
                            "INFO" if status == "취소" else "ERROR"))
    parties = defaultdict(list)
    for role in ROLES:
        name = company_name(row.get(role + "_name"))
        number = business_number(row.get(role))
        state = row.get(role + "_state")
        member = members.get(number)
        if role == "maker":
            if (not member or member.get("kind") not in ("생산자", "복수")
                    or state in NON_MEMBER_STATES or state == "원본에 없음"):
                issues.append(issue("D048", role, "제조자를 생산자 회원사 명부에서 선택하세요."))
                continue
            if policy and not eligible(member, day(policy["cutoff"])):
                issues.append(issue("D049", role, "제조자는 기준일에 회원 자격이 있는 생산자 회원사여야 합니다."))
                continue
        if state == "원본에 없음" and not number and not name:
            continue
        if not valid_number(number):
            issues.append(issue("D043" if number else "D047", role, "역할의 사업자번호 또는 원본의 정보부재를 확인하세요."))
            continue
        if member:
            known = [member["name"], *clean(member.get("aliases")).split("|")]
            if not name or name not in {company_name(x) for x in known if clean(x)}:
                issues.append(issue("D044", role + "_name", "사업자번호와 회원사명 또는 별칭을 대조하세요."))
            if state in NON_MEMBER_STATES:
                issues.append(issue("D045", role + "_state", "회원사 명부에 존재하는 사업자입니다."))
        elif state not in NON_MEMBER_STATES or not name:
            issues.append(issue("D047", role + "_state", "사업자명과 회원 여부를 확인하세요."))
        parties[number].append(role)
    for roles in parties.values():
        if len(roles) > 1:
            issues.append(issue("D085" if "maker" in roles else "D084", roles[0],
                                "동일 사업자의 역할을 합쳐 직접수출을 우선 적용합니다.", "INFO"))
    if not parties and not any(p["field"].startswith(ROLES) for p in issues):
        issues.append(issue("D014", "agent", "원본에서 확인 가능한 사업자 역할을 입력하세요."))
    return issues, parties


def eligible(member, cutoff):
    approved, withdrawn = day(member.get("approved")), day(member.get("withdrawn"))
    return bool(approved and approved <= cutoff and (not withdrawn or cutoff < withdrawn))


def attribute(parties, members, cutoff, usd, kg):
    results = []
    for number, roles in sorted(parties.items()):
        member = members.get(number)
        active = bool(member and eligible(member, cutoff))
        direct = active and any(role in roles for role in ("agent", "owner"))
        indirect = active and "maker" in roles
        kind = "직접" if direct else "간접" if indirect else ""
        reason = ("직접 우선 적용" if direct and indirect else "회원사 역할 인정" if kind
                  else "기준일 회원자격 없음" if member else NON_MEMBER)
        results.append(dict(number=number, name=member["name"] if member else "", roles=roles,
                            direct=direct, indirect=indirect, kind=kind,
                            usd=str(usd if kind else 0), kg=None if kind and kg is None else str(kg if kind else 0),
                            reason=reason))
    return results


def declaration_key(row):
    return clean(row.get("number")).replace("-", "").upper(), int(row["line"])


def line_fingerprint(row):
    ignored = {"id", "source", "revision", "number", "line", "version"}
    values = {}
    for key in row.keys() - ignored:
        value = clean(row[key])
        if not value:
            continue
        if key in ("usd", "kg", "fob", "quantity"):
            parsed = decimal(value)
            value = parsed if parsed is not None else value
        elif key in ("declared", "shipped", "accepted"):
            value = day(value) or value
        elif key in ROLES:
            value = business_number(value)
        elif key in {role + "_name" for role in ROLES}:
            value = company_name(value)
        elif key in {role + "_state" for role in ROLES} and value in NON_MEMBER_STATES:
            value = NON_MEMBER
        elif key == "hs":
            value = re.sub(r"[.\s-]", "", value)
        values[key] = value
    return tuple(sorted(values.items()))


def calculate(rows, members, policy):
    index = {business_number(member["number"]): member for member in members}
    cutoff = day(policy["cutoff"])
    groups = defaultdict(list)
    checks, decisions, ledger = [], [], []
    actual_usd = actual_kg = Decimal(0)
    member_issues = [dict(problem, member_id=m["id"]) for m in members
                     for problem in validate_member(m, ())]
    for row in rows:
        date_value = day(row.get(policy["basis"]))
        relevant = not date_value or date_value.year == policy["year"]
        issues, parties = validate_line(row, index, policy) if relevant else ([], {})
        if relevant:
            checks.extend(dict(problem, row_id=row["id"]) for problem in issues)
        if all(re.fullmatch(r"[0-9]{1,8}", clean(row.get(k))) and int(row[k]) > 0
               for k in ("line", "version")) and clean(row.get("number")):
            groups[declaration_key(row)].append((row, issues, parties, relevant))
    accepted_lines = 0
    missing_kg = 0
    for key, candidates in sorted(groups.items()):
        if not any(item[3] for item in candidates):
            continue
        versions = {int(row["version"]) for row, *_ in candidates}
        if len(versions) > 1:
            # Revision numbers alone do not prove the replacement scope.
            checks.extend(dict(issue("D083", "version", "같은 신고번호와 품목란에 서로 다른 정정본이 있습니다. 원본을 대조해 유효한 신고를 확인하고, 대체된 행은 원장에서 제외하세요."),
                               row_id=row["id"]) for row, *_ in candidates)
            continue
        fingerprints = {line_fingerprint(row) for row, *_ in candidates}
        if len(fingerprints) > 1:
            checks.extend(dict(issue("D012", "usd", "같은 신고번호와 품목란에 입력 내용이 다른 행이 있습니다. 원본에서 품목란 번호와 입력값을 확인하세요."),
                               row_id=row["id"]) for row, *_ in candidates)
            continue
        row, issues, parties, _ = candidates[0]
        for duplicate, *_ in candidates[1:]:
            checks.append(dict(issue("D080", "line", "동일한 신고 품목란을 1건으로 합산했습니다.", "INFO"),
                               row_id=duplicate["id"]))
        if row.get("scope") == "제외" or row.get("status") == "취소":
            continue
        if member_issues or any(p["severity"] != "INFO" for p in issues):
            continue
        usd, kg = decimal(row["usd"]), decimal(row.get("kg"))
        actual_usd += usd
        if kg is None:
            missing_kg += 1
        else:
            actual_kg += kg
        accepted_lines += 1
        evidence = dict(row_id=row["id"], number_declared=key[0], line=key[1],
                        version=int(row["version"]), source_ids=[item[0]["id"] for item in candidates])
        for result in attribute(parties, index, cutoff, usd, kg):
            record = dict(result, **evidence)
            decisions.append(record)
            if result["kind"]:
                ledger.append(record)
    return dict(checks=checks, member_issues=member_issues, decisions=decisions, ledger=ledger,
                accepted_lines=accepted_lines, actual_usd=str(actual_usd), actual_kg=None if missing_kg else str(actual_kg),
                ready=not member_issues and not any(p["severity"] != "INFO" for p in checks))


def summarize(ledger):
    groups = {}
    for row in ledger:
        key = row["number"]
        group = groups.setdefault(key, dict(number=key, name=row["name"], direct_usd=Decimal(0),
                                             indirect_usd=Decimal(0), direct_kg=Decimal(0),
                                             indirect_kg=Decimal(0), lines=0))
        kind = "direct" if row["kind"] == "직접" else "indirect"
        group[kind + "_usd"] += Decimal(row["usd"])
        kg_key = kind + "_kg"
        if row["kg"] is None:
            group[kg_key] = None
        elif group[kg_key] is not None:
            group[kg_key] += Decimal(row["kg"])
        group["lines"] += 1
    return [dict(group, total_usd=group["direct_usd"] + group["indirect_usd"]) for group in groups.values()]
