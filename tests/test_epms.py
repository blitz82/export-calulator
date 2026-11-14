from decimal import Decimal
from io import BytesIO
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from epms import books, rules, store
from epms.api import create_app

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def number(seed):
    return next(seed + str(d) for d in range(10) if rules.valid_number(seed + str(d)))


NUMBERS = {k: number(f"12345678{i}") for i, k in enumerate("ABCX", 1)}
MEMBERS = [dict(id=i, name=k, number=NUMBERS[k], approved="2024-01-01",
                kind=("생산자" if i % 2 else "수출업체")) for i, k in enumerate("ABC", 1)]
POLICY = dict(year=2026, basis="shipped", cutoff="2026-12-31", hs=["0806100000"])


def line(roles="ABC", **overrides):
    row = dict(id=1, number="12345-26-123456", line="1", version="1",
               declared="2026-01-01", shipped="2026-01-02", usd="10000.01", kg="2.01",
               hs="0806.10.0000", scope="대상", status="유효")
    for role, company in zip(rules.ROLES, roles):
        row.update({role:NUMBERS[company],role+"_name":company,
                    role+"_state":"회원사 아님" if company=="X" else "회원 대조"})
    return row | overrides


@pytest.mark.parametrize(("roles", "expected"), [
    ("AAA", {"A":"직접"}), ("ABA", {"A":"직접","B":"직접"}),
    ("ABC", {"A":"직접","B":"직접","C":"간접"}), ("XBC", {"B":"직접","C":"간접"}),
    ("AXC", {"A":"직접","C":"간접"}), ("XXC", {"C":"간접"}),
    ("AAC", {"A":"직접","C":"간접"}), ("ACC", {"A":"직접","C":"직접"}),
])
def test_roles(roles, expected):
    result = rules.calculate([line(roles, usd="10000")], MEMBERS, POLICY)
    assert result["ready"]
    assert {r["name"]:r["kind"] for r in result["ledger"]} == expected
    assert all(r["usd"] == "10000" and r["kg"] == "2.01" for r in result["ledger"])
    assert result["actual_usd"] == "10000" and result["actual_kg"] == "2.01"
    if "X" in roles:
        decision = next(r for r in result["decisions"] if r["number"] == NUMBERS["X"])
        assert decision["roles"] == [role for role, company in zip(rules.ROLES, roles) if company == "X"]
        assert decision["usd"] == decision["kg"] == "0"
        assert not decision["direct"] and not decision["indirect"]


@pytest.mark.parametrize(("roles", "omitted", "member"), [
    ("XBC", "agent", "B"),
    ("AXC", "owner", "A"),
])
def test_non_member_number_optional_with_confirmed_exporter(roles, omitted, member):
    result = rules.calculate([line(roles, **{omitted: ""})], MEMBERS, POLICY)
    assert result["ready"] and result["accepted_lines"] == 1
    assert {r["name"]: r["kind"] for r in result["ledger"]} == {member:"직접", "C":"간접"}


@pytest.mark.parametrize("row", [
    line("XXC", agent=""),
    line("XXC", agent="", owner=""),
    line("AXC", agent_name="불일치", owner=""),
    line("AXC", owner="", owner_state=""),
    line("AXC", owner="123"),
    line("AXC", owner="", owner_name=""),
    line("AXC", owner="", maker="", maker_name=""),
])
def test_number_omission_does_not_bypass_validation(row):
    result = rules.calculate([row], MEMBERS, POLICY)
    assert not result["ready"] and result["accepted_lines"] == 0 and not result["ledger"]


def test_legacy_non_member_state_matches_new_state():
    result = rules.calculate([line("XBC"), line("XBC", id=2, agent_state="비회원 확인")], MEMBERS, POLICY)
    assert result["ready"] and result["accepted_lines"] == 1
    assert {r["name"] for r in result["ledger"]} == {"B", "C"}


def test_member_cannot_be_marked_non_member():
    result = rules.calculate([line(agent_state="회원사 아님")], MEMBERS, POLICY)
    assert "D045" in {p["code"] for p in result["checks"]}
    assert not result["ready"] and not result["ledger"]


@pytest.mark.parametrize(("row", "code"), [
    (line("ABX"), "D048"),
    (line("XXX"), "D048"),
    (line("ABB"), "D048"),
    (line(maker="", maker_name="", maker_state="원본에 없음"), "D048"),
    (line(maker_state="비회원 확인"), "D048"),
    (line(maker_name="다른 업체"), "D044"),
])
def test_unconfirmed_producer_holds_entire_line(row, code):
    result = rules.calculate([row], MEMBERS, POLICY)
    assert not result["ready"] and result["accepted_lines"] == 0
    assert result["ledger"] == result["decisions"] == []
    assert result["actual_usd"] == result["actual_kg"] == "0"
    assert code in {problem["code"] for problem in result["checks"]}


def test_producer_requires_eligibility_on_cutoff():
    members = [*MEMBERS[:2], MEMBERS[2] | {"kind":"복수"}]
    assert rules.calculate([line()], members, POLICY)["ready"]
    members[2] |= {"withdrawn":POLICY["cutoff"]}
    result = rules.calculate([line()], members, POLICY)
    assert not result["ready"] and result["accepted_lines"] == 0 and not result["ledger"]
    assert "D049" in {problem["code"] for problem in result["checks"]}


def test_dedup_and_distinct_lines():
    result = rules.calculate([line(), line(id=2, usd="10,000.010"), line(id=3, line="2")], MEMBERS, POLICY)
    assert result["ready"] and result["accepted_lines"] == 2
    assert Decimal(result["actual_usd"]) == Decimal("20000.02")
    assert len(result["ledger"]) == 6
    assert result["ledger"][0]["source_ids"] == [1, 2]


@pytest.mark.parametrize("change,code", [({"version":"2"},"D083"), ({"usd":"7"},"D012"),
                                         ({"shipped":"2025-01-03"},"D012")])
def test_conflict_holds_entire_line(change, code):
    result = rules.calculate([line(), line(id=2, **change)], MEMBERS, POLICY)
    assert not result["ready"]
    assert not result["ledger"]
    assert code in {p["code"] for p in result["checks"]}


def test_cutoff_and_missing_evidence():
    members = [MEMBERS[0] | {"withdrawn":"2026-12-31"}, *MEMBERS[1:]]
    assert not any(r["name"] == "A" for r in rules.calculate([line()], members, POLICY)["ledger"])
    assert not rules.calculate([line(agent="")], MEMBERS, POLICY)["ready"]
    assert not rules.calculate([line(line="")], MEMBERS, POLICY)["ledger"]


@pytest.mark.filterwarnings("ignore:Cell .* is marked as a date")
def test_sample_profiles():
    paths = sorted(SAMPLES.glob("(*.xlsx"))
    counts = [len(books.read_rows(path.read_bytes(), books.LINE)[2]) for path in paths]
    assert counts == [314, 264, 1384, 561, 473, 42, 1170, 3402, 3670]


@pytest.mark.parametrize("count", [10_000, 50_000])
def test_large_calculation(count):
    rows = [line(id=i, number=f"12345-26-{i:06d}") for i in range(count)]
    start = perf_counter()
    result = rules.calculate(rows, MEMBERS, POLICY)
    print(f"합성 계산 {count:,}행 {perf_counter()-start:.3f}초")
    assert result["accepted_lines"] == count
    assert Decimal(result["actual_usd"]) == Decimal("10000.01") * count
    assert len(result["ledger"]) == count * 3


def client_for(path):
    app = create_app(path)
    client = TestClient(app)
    token = client.get("/api/session").json()["token"]
    client.headers["x-epms-token"] = token
    return client, app


def test_member_import_recovers_previously_rejected_workbook(tmp_path):
    client, app = client_for(tmp_path / "live.db")
    client.post("/api/organizations", params={"name": "회원사 가져오기"}).raise_for_status()
    base = "/api/org/1"
    producer, exporter, invalid = [
        {key: value for key, value in member.items() if key != "id"} for member in MEMBERS
    ]
    producer.update(kind="생산", email="개인우편")
    exporter.update(kind="수출")
    invalid.update(email="invalid")
    source = books.workbook([("회원사명부", books.MEMBER, [producer, exporter, invalid])])
    sheet, _, rows = books.read_rows(source, books.MEMBER)
    with app.state.db.begin() as conn:
        file_id = conn.execute(store.import_files.insert().values(
            org=1, sha=books.digest(source), kind="members", name="명부.xlsx", data=source,
        )).inserted_primary_key[0]
        conn.execute(store.import_rows.insert(), [
            dict(file=file_id, sheet=sheet, row=row_index, original=values) for row_index, values in rows
        ])

    def upload():
        response = client.post(base + "/import/members", params={"name": "명부.xlsx"}, content=source)
        response.raise_for_status()
        return response.json()

    first = upload()
    assert (first["imported"], first["skipped"]) == (2, 0)
    assert [(row["row"], row["issues"][0]["code"]) for row in first["rejected"]] == [(4, "M012")]
    saved = client.get(base + "/data/members").json()
    assert [row["kind"] for row in saved] == ["생산자", "수출업체"]
    assert saved[0]["email"] == ""
    retry = upload()
    assert (retry["imported"], retry["skipped"], retry["rejected"]) == (0, 2, first["rejected"])
    assert client.get(base + "/data/members").json() == saved
    with app.state.db.connect() as conn:
        originals = conn.execute(store.import_rows.select().order_by(store.import_rows.c.row)).all()
    assert [row.original for row in originals] == [values for _, values in rows]


def test_member_standard_template_keeps_validations_and_imports(tmp_path):
    client, _ = client_for(tmp_path / "live.db")
    client.post("/api/organizations", params={"name": "표준양식 검증"}).raise_for_status()
    base = "/api/org/1"
    response = client.get(base + "/export/members", params={"template": "true"})
    response.raise_for_status()
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames == ["회원사명부", "작성·AI 변환 안내"]
    sheet = book.worksheets[0]
    assert [cell.value for cell in sheet[1]] == list(books.MEMBER.values())
    validations = sheet.data_validations.dataValidation
    assert len(validations) == len(books.MEMBER)
    assert all(rule.showErrorMessage and rule.errorStyle == "stop" and len(rule.formula1) <= 255
               for rule in validations)
    kind = next(rule for rule in validations if "C2" in rule)
    assert kind.type == "list" and kind.formula1 == '"생산자,수출업체,복수"' and "C50001" in kind
    assert sheet.column_dimensions["B"].number_format == "@"
    assert sheet.column_dimensions["E"].number_format == "yyyy-mm-dd"
    assert book.worksheets[1]["A4"].value == "AI에 전달할 요청"
    sheet.append([MEMBERS[0].get(key) for key in books.MEMBER])
    completed = BytesIO()
    book.save(completed)
    result = client.post(base + "/import/members", params={"name": "작성.xlsx"}, content=completed.getvalue())
    assert result.json()["imported"] == 1


def test_line_import_saves_incomplete_rows_for_correction(tmp_path):
    client, _ = client_for(tmp_path / "review.db")
    client.post("/api/organizations", params={"name": "원장 보완"}).raise_for_status()
    base = "/api/org/1"
    for member in MEMBERS:
        values = {key: value for key, value in member.items() if key != "id"}
        assert client.post(base + "/data/members", json=[{"values": values}]).json()[0]["row"]
    incomplete = line(line="", version="", scope="", status="")
    incomplete.pop("id")
    source = books.workbook([("원장", books.LINE, [incomplete])])
    result = client.post(base + "/import/lines", params={"name": "원장.xlsx"}, content=source).json()
    assert (result["imported"], result["needs_review"], result["rejected"]) == (1, 1, [])
    saved, = client.get(base + "/data/lines").json()
    assert {problem["code"] for problem in client.get(base + "/line-checks").json()[str(saved["id"])]} >= {
        "D003", "D014", "D064", "D010",
    }
    calculated = rules.calculate([saved], MEMBERS, POLICY)
    assert calculated["accepted_lines"] == 0 and not calculated["ready"]
    update = client.post(base + "/data/lines", json=[{
        "id": saved["id"], "revision": saved["revision"], "values": incomplete | {"scope": "대상"},
    }]).json()[0]
    assert update["row"]["scope"] == "대상"
    assert any(problem["code"] == "D003" for problem in update["issues"])


@pytest.mark.parametrize("exclusion", [{"scope": "제외"}, {"status": "취소"}])
def test_excluded_declaration_can_be_saved_and_does_not_block_matching_row(tmp_path, exclusion):
    client, _ = client_for(tmp_path / "excluded.db")
    client.post("/api/organizations", params={"name": "원장 제외"}).raise_for_status()
    base = "/api/org/1"
    response = client.post(base + "/data/lines", json=[{"values": exclusion}])
    response.raise_for_status()
    saved = response.json()[0]["row"]
    checks = client.get(base + "/line-checks").json()[str(saved["id"])]
    assert checks and all(problem["severity"] == "INFO" for problem in checks)

    active = line()
    excluded = {key: active[key] for key in ("number", "line", "version")} | exclusion | {"id": 3}
    other_version = excluded | {"id": 4, "version": "2"}
    result = rules.calculate([active, saved | {"id": 2}, excluded, other_version], MEMBERS, POLICY)
    assert result["ready"]
    assert result["accepted_lines"] == 1
    assert result["actual_usd"] == active["usd"]
    assert all(item["source_ids"] == [active["id"]] for item in result["ledger"])


def test_workflow_and_restore(tmp_path):
    client, app = client_for(tmp_path / "live.db")
    assert client.get("/api/organizations", headers={"x-epms-token":"bad"}).status_code == 403
    assert client.post("/api/organizations", params={"name":"합성 검증"}).status_code == 200
    base = "/api/org/1"
    editable = lambda row: {"values":{k:v for k,v in row.items() if k != "id"}}
    for member in MEMBERS:
        result = client.post(base+"/data/members", json=[editable(member)]).json()[0]
        assert result["row"]
    result = client.post(base+"/data/members", json=[editable(MEMBERS[0])]).json()[0]
    assert [item["code"] for item in result["issues"]] == ["M005"]
    source = books.workbook([("합성", books.LINE, [line("ABA")])])
    url = base+"/import/lines"
    assert client.post(url, params={"name":"합성.xlsx"}, content=source).json()["imported"] == 1
    assert client.post(url, params={"name":"복제.xlsx"}, content=source).status_code == 409
    older = line("ABC", number="12345-25-123456", shipped="2025-01-02", declared="2025-01-01")
    client.post(base+"/data/lines", json=[editable(older)]).raise_for_status()
    run_ids = []
    for year in (2025,2026):
        assert client.put(base+f"/policies/{year}", json=POLICY | {"reason":"합성 테스트"}).status_code == 200
        result = client.post(base+"/runs", params={"year":year}).json()
        assert result["ready"]
        run_ids.append(result["id"])
    current = base+f"/runs/{run_ids[1]}"
    detail = client.get(current).json()
    assert detail["accepted_lines"] == 1
    assert {r["name"]: r["kind"] for r in detail["ledger"]} == {"A":"직접","B":"직접"}
    evidence = client.get(current+"/evidence/1").json()
    assert evidence["source"]["sha"] == books.digest(source)
    assert evidence["decisions"][0]["indirect"]
    bundle = client.get(current+"/report", params={"bundle":True})
    assert len(ZipFile(BytesIO(bundle.content)).namelist()) == 2
    params = dict(first_id=run_ids[0], second_id=run_ids[1], sheet="코파(파프리카)", final=True)
    readiness = client.get(base+"/submission-readiness",
                           params={key:value for key,value in params.items() if key != "final"}).json()
    assert not readiness["saved"] and all(not entry["blocker"] for entry in readiness["years"].values())
    output = client.post(base+"/submission", params=params)
    assert output.status_code == 200, output.text if output.status_code != 200 else ""
    workbook = load_workbook(BytesIO(output.content))
    ws = workbook[params["sheet"]]
    original = load_workbook(next(SAMPLES.glob("2027*.xlsx")))[ws.title]
    assert ws.merged_cells.ranges == original.merged_cells.ranges
    assert ws["L7"].value == 10000.01 and ws["O7"].value == 10000.01
    assert ws["M9"].value == 10000.01 and ws["P9"].value == 0
    assert ws["B2"].value == "최종 확정본"
    annual_path = base+"/annual-submission"
    annual_params = {"run_id":run_ids[1], "previous_id":run_ids[0]}
    assert client.get(annual_path, params=annual_params).json()["blocker"] is None
    annual = client.post(annual_path, params=annual_params | {"final":True}).raise_for_status()
    annual_book = load_workbook(BytesIO(annual.content))
    assert annual_book.sheetnames == ["품목별 HS코드", "합성 검증 2026", "계산 및 검증 정보"]
    assert annual_book["합성 검증 2026"]["L7"].value == 10000.01
    assert annual_book["합성 검증 2026"]["O7"].value == 10000.01
    assert client.get(annual_path, params=annual_params).json()["saved"]
    backup = client.get("/api/backup")
    restored = tmp_path / "restored.db"
    restored.write_bytes(backup.content)
    copy_client, copy_app = client_for(restored)
    assert copy_client.get(current).json() == detail
    assert copy_client.get(base+"/audit").json() == client.get(base+"/audit").json()
    assert copy_client.post(base+"/submission", params=params).content == output.content
    copy_app.state.db.dispose()
    app.state.db.dispose()


def test_xlsx_untrusted_text():
    data = books.workbook([("확인", {"name":"회원사"}, [{"name":"=1+1"}])])
    cell = load_workbook(BytesIO(data)).active["A2"]
    assert cell.value == "=1+1" and cell.data_type == "s"


def test_submission_amount_without_optional_weight_or_other_date():
    result = rules.calculate([line(kg="", declared="")], MEMBERS, POLICY)
    assert result["ready"] and result["actual_usd"] == "10000.01"
    assert result["actual_kg"] is None
    assert rules.summarize(result["ledger"])[0]["direct_kg"] is None
    assert not rules.calculate([line(shipped="")], MEMBERS, POLICY)["ready"]
    assert any(issue["field"] == "shipped" for issue in rules.validate_line(line(shipped=""), {})[0])
    alternate = line(hs="0805220000")
    assert rules.calculate([alternate], MEMBERS, POLICY | {"hs":[*POLICY["hs"], "0805220000"]})["ready"]


def test_reset_scoped_and_reimportable(tmp_path):
    client, app = client_for(tmp_path / "reset.db")
    client.post("/api/organizations", params={"name":"대상"}).raise_for_status()
    assert client.post("/api/organizations", params={"name":"다른 조직"}).status_code == 409
    with app.state.db.begin() as conn:
        conn.execute(store.organizations.insert().values(name="기존 다른 조직"))
        conn.execute(store.office.delete())
    assert client.get("/api/office").json()["item"] is None
    assert client.put("/api/office/1").raise_for_status().json()["id"] == 1
    assert client.put("/api/office/2").status_code == 409
    source = books.workbook([("합성", books.LINE, [line()])])
    for org in (1, 2):
        client.post(f"/api/org/{org}/import/lines", params={"name":"원본.xlsx"}, content=source).raise_for_status()
    path = "/api/org/1/data/lines"
    assert client.post(path+"/reset", json={"confirmation":"모두리셋 "}).status_code == 400
    assert len(client.get(path).json()) == 1
    assert client.put("/api/office/1", params={"name":"수정 품목"}).raise_for_status().json()["id"] == 1
    assert client.get("/api/office").json()["item"]["name"] == "수정 품목"
    assert len(client.get(path).json()) == 1
    assert client.post(path+"/reset", json={"confirmation":"모두리셋"}).json() == {"count":1}
    assert client.get(path).json() == []
    assert len(client.get("/api/org/2/data/lines").json()) == 1
    assert client.get("/api/org/1/audit").json()[0]["reason"] == "원장 전체 초기화"
    assert client.post("/api/org/1/import/lines", params={"name":"원본.xlsx"}, content=source).json()["imported"] == 1
    app.state.db.dispose()
