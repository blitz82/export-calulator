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
                    role+"_state":"비회원 확인" if company=="X" else "회원 대조"})
    return row | overrides


@pytest.mark.parametrize(("roles", "expected"), [
    ("AAA", {"A":"직접"}), ("ABA", {"A":"직접","B":"직접"}),
    ("ABC", {"A":"직접","B":"직접","C":"간접"}), ("XBC", {"B":"직접","C":"간접"}),
    ("XXC", {"C":"간접"}), ("AAB", {"A":"직접","B":"간접"}),
    ("ABB", {"A":"직접","B":"직접"}), ("XXX", {}),
])
def test_roles(roles, expected):
    result = rules.calculate([line(roles, usd="10000")], MEMBERS, POLICY)
    assert result["ready"]
    assert {r["name"]:r["kind"] for r in result["ledger"]} == expected
    assert all(r["usd"] == "10000" and r["kg"] == "2.01" for r in result["ledger"])


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
    if not paths:
        pytest.skip("Local historical workbooks are required for this regression test.")
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
