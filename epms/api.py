import json
import os
import secrets
import sys
from collections import defaultdict
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from epms import books, contract_gate, ocr, ocr_profiles, rules, store, submission

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"members": books.MEMBER, "lines": books.LINE}
TABLES = {"members": store.memberships, "lines": store.declarations}
SUMMARY = dict(number="사업자번호", name="회원사명", direct_usd="직접수출 USD", indirect_usd="간접수출 USD",
               total_usd="합계 USD", direct_kg="직접수출 kg", indirect_kg="간접수출 kg", lines="인정 품목란 수")
LEDGER = dict(number_declared="신고번호", line="품목란", version="정정 버전", number="사업자번호", name="회원사명",
              direct="직접 자격", indirect="간접 자격", kind="최종 인정", usd="USD", kg="kg", reason="판정 근거",
              row_id="원장 ID", source_ids="관련 원장 ID")
CHECKS = dict(row_id="원장 ID", member_id="회원 ID", code="오류 코드", field="항목", severity="심각도", message="검증 내용")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Edit(BaseModel):
    values: dict[str, str] = Field(max_length=50)
    id: int | None = None
    revision: int | None = None
    reason: str = Field(default="화면 입력", min_length=1, max_length=500)


class Policy(BaseModel):
    cutoff: str
    basis: str
    hs: list[str] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


class ResetLedger(BaseModel):
    confirmation: str


class OcrRow(BaseModel):
    page: int = Field(ge=1, le=ocr.MAX_PAGES)
    values: dict[str, str] = Field(max_length=50)
    verified_usd: str = Field(max_length=100)
    verified_kg: str = Field(max_length=100)


class OcrSelection(BaseModel):
    box: tuple[float, float, float, float]


def fail(message, status=400):
    raise HTTPException(status, message)


def download(data, name, media=XLSX):
    return Response(data, media_type=media, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})


def create_app(path=None):
    db = store.engine_for(path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(24)
    app.state.token = token
    app.state.db = db

    @app.middleware("http")
    async def local(request, call_next):
        if request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
            return Response(status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/session":
            if not secrets.compare_digest(request.headers.get("x-epms-token", ""), token):
                return JSONResponse({"detail": "프로그램 화면을 새로고침하세요."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        return JSONResponse({"detail": "입력값의 형식 또는 길이를 확인하세요."}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def conflict(_, exc):
        return JSONResponse({"detail": "중복 등록 또는 참조 관계를 확인하세요."}, status_code=409)

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.post("/api/shutdown")
    def shutdown(tasks: BackgroundTasks):
        stop = getattr(app.state, "shutdown", None)
        if stop is None:
            fail("이 실행 환경에서는 화면에서 프로그램을 종료할 수 없습니다.", 409)
        tasks.add_task(stop)
        return {"ok": True}

    @app.get("/api/fields")
    def fields():
        return FIELDS

    def actor(request):
        return rules.clean(request.headers.get("x-epms-actor"))[:80] or os.environ.get("USERNAME", "로컬 사용자")

    def table_for(kind):
        if kind not in TABLES:
            fail("자료 종류를 확인하세요.", 404)
        return TABLES[kind]

    def organization(conn, org):
        result = conn.execute(select(store.organizations.c.name).where(store.organizations.c.id == org)).scalar()
        if result is None:
            fail("통합조직을 선택하세요.", 404)
        return result

    def member_index(conn, org):
        return {rules.business_number(m["number"]): m for m in store.records(conn, store.memberships, org)}

    def validate(conn, org, kind, row):
        index = member_index(conn, org)
        if kind == "members":
            return rules.validate_member(row, index.values())
        problems = rules.validate_line(row, index)[0] + contract_gate.evaluate(row, index, contract_gate.context(conn, org))[0]
        if rules.active_declaration_key(row) is not None:
            declarations = declaration_index(store.records(conn, store.declarations, org))
            problems.extend(declaration_issues(row, declarations, row["id"]))
        return problems

    @app.get("/api/organizations")
    def organizations():
        with db.connect() as conn:
            return [dict(row) for row in conn.execute(store.organizations.select().order_by(store.organizations.c.id)).mappings()]

    @app.post("/api/organizations")
    def add_organization(name: str, request: Request):
        name = rules.clean(name)
        if not name or len(name) > 100:
            fail("품목명을 100자 이내로 입력하세요.")
        with db.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            if conn.execute(select(store.organizations.c.id).limit(1)).first():
                fail("이미 품목이 등록되어 있습니다. 프로그램 설정에서 품목명을 수정하세요.", 409)
            result = conn.execute(store.organizations.insert().values(name=name))
            org = result.inserted_primary_key[0]
            conn.execute(store.office.insert().values(id=1, org=org))
            store.log(conn, org, actor(request), "통합조직", org, "신규 등록", None, {"name": name})
        return {"id": org, "name": name}

    @app.get("/api/office")
    def get_office():
        with db.connect() as conn:
            active = conn.execute(select(store.office.c.org).where(store.office.c.id == 1)).scalar()
            rows = [dict(row) for row in conn.execute(store.organizations.select().order_by(store.organizations.c.id)).mappings()]
        return {"item": next((row for row in rows if row["id"] == active), rows[0] if len(rows) == 1 else None),
                "choices": rows if active is None and len(rows) > 1 else []}

    @app.put("/api/office/{org}")
    def update_office(org: int, request: Request, name: str | None = None):
        with db.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            old = organization(conn, org)
            active = conn.execute(select(store.office.c.org).where(store.office.c.id == 1)).scalar()
            if active is not None and active != org:
                fail("사용 중인 품목만 수정할 수 있습니다.", 409)
            if name is None:
                name = old
            else:
                name = rules.clean(name)
                if not name or len(name) > 100:
                    fail("품목명을 100자 이내로 입력하세요.")
            if name != old:
                conn.execute(store.organizations.update().where(store.organizations.c.id == org).values(name=name))
            if active is None:
                conn.execute(store.office.insert().values(id=1, org=org))
            if active is None or name != old:
                store.log(conn, org, actor(request), "통합조직", org,
                          "사용 품목 지정" if active is None else "품목명 수정", {"name": old}, {"name": name})
        return {"id": org, "name": name}

    @app.get("/api/org/{org}/data/{kind}")
    def list_rows(org: int, kind: str):
        table = table_for(kind)
        with db.connect() as conn:
            organization(conn, org)
            rows = store.records(conn, table, org)
            if kind == "lines":
                pdfs = line_pdfs(conn, org)
                return [dict(row, pdf=pdfs.get(row["id"])) for row in rows]
            return rows

    @app.post("/api/org/{org}/data/{kind}/validate")
    def preview(org: int, kind: str, edit: Edit):
        table_for(kind)
        with db.connect() as conn:
            organization(conn, org)
            return validate(conn, org, kind, dict(edit.values, id=edit.id))

    @app.get("/api/org/{org}/line-checks")
    def line_checks(org: int):
        with db.connect() as conn:
            organization(conn, org)
            index = member_index(conn, org)
            rows = store.records(conn, store.declarations, org)
            agreements = contract_gate.context(conn, org)
        declarations = declaration_index(rows)
        return {row["id"]: problems for row in rows
                if (problems := rules.validate_line(row, index)[0] + contract_gate.evaluate(row, index, agreements)[0] + declaration_issues(row, declarations, row["id"]))}

    def declaration_index(rows):
        index = defaultdict(dict)
        for row in rows:
            key = rules.active_declaration_key(row)
            if key is not None:
                index[key][row["id"]] = row
        return index

    def declaration_issues(values, declarations, edit_id):
        candidates = declarations.get(rules.active_declaration_key(values), {})
        if not candidates or (len(candidates) == 1 and edit_id in candidates):
            return []
        version = rules.declaration_version(values)
        incoming = dict(rules.line_fingerprint(values))
        problems = []
        for row_id, existing in candidates.items():
            if row_id == edit_id:
                continue
            previous = dict(rules.line_fingerprint(existing))
            label = f'기존 원장 #{row_id} (신고번호 {existing["number"][:80]}, 품목란 {existing["line"]})'
            same_version = version == rules.declaration_version(existing)
            if same_version and incoming == previous:
                problem = rules.issue("D080", "line", f"{label}와 중복됩니다. 원본을 확인하고 중복 행을 수정하거나 제외하세요.")
            else:
                changes = [
                    f'{books.LINE[field]} {str(existing.get(field) or "공란")[:80]} → {str(values.get(field) or "공란")[:80]}'
                    for field in books.LINE if previous.get(field) != incoming.get(field)
                ]
                if not same_version:
                    changes.insert(0, f'정정 버전 {existing.get("version") or "공란"} → {values.get("version") or "공란"}')
                    code, field = "D083", "version"
                else:
                    code, field = "D012", "line"
                problem = rules.issue(code, field, f'{label}와 충돌합니다. 기존 값 → 입력 값 ({", ".join(changes)}). '
                                      "원본을 확인하고 기존 행을 수정하거나 대체된 행을 제외하세요.")
            problems.append(dict(problem, row_id=row_id))
        return problems

    def save(conn, org, kind, edit, user, source=None, index=None, declarations=None):
        table = table_for(kind)
        values = {key: rules.clean(value) for key, value in edit.values.items() if key in FIELDS[kind]}
        if kind == "lines" and "line" in values:
            values["line"] = rules.format_line_number(values["line"])
        if any(len(v) > 4000 for v in values.values()):
            fail("셀 입력값은 4,000자 이하여야 합니다.")
        old = None
        if edit.id is not None:
            old = conn.execute(table.select().where(table.c.org == org, table.c.id == edit.id)).first()
            if old is None or (kind == "lines" and old.deleted):
                fail("자료를 찾을 수 없습니다.", 404)
            if old.revision != edit.revision:
                fail("다른 화면에서 변경했습니다. 새로고침하세요.", 409)
        row = dict(values, id=edit.id)
        if index is None:
            index = member_index(conn, org)
        issues = (rules.validate_member(row, index.values()) if kind == "members"
                  else rules.validate_line(row, index)[0] + contract_gate.evaluate(row, index, contract_gate.context(conn, org))[0])
        if kind == "members" and any(p["severity"] == "ERROR" for p in issues):
            return {"issues": issues}
        key = rules.active_declaration_key(values) if declarations is not None else None
        if key is not None:
            issues.extend(declaration_issues(values, declarations, edit.id))
        revision = (old.revision if old else 0) + 1
        record = dict(org=org, revision=revision, data=values)
        if kind == "members":
            number = rules.business_number(values["number"])
            if old and number != old.number:
                fail("기존 회원의 사업자번호 정정은 관리자 확인이 필요합니다.")
            conn.execute(insert(store.companies).values(number=number).on_conflict_do_nothing())
            record["number"] = number
        elif not old:
            record["source"] = source
        if old:
            changed = conn.execute(table.update().where(table.c.id == edit.id,
                                   table.c.revision == old.revision).values(**record))
            if changed.rowcount != 1:
                fail("다른 화면에서 변경했습니다. 새로고침하세요.", 409)
            row_id = edit.id
        else:
            if kind == "members":
                # Keep deleted member IDs reserved so a stale editor cannot target a new member.
                last_deleted = select(func.coalesce(func.max(store.audit.c.target), 0)).where(
                    store.audit.c.entity == "members", store.audit.c.reason == "회원사 삭제",
                ).scalar_subquery()
                last_member = select(func.coalesce(func.max(table.c.id), 0)).scalar_subquery()
                record["id"] = func.max(last_member, last_deleted) + 1
            row_id = conn.execute(table.insert().values(**record)).inserted_primary_key[0]
        row = dict(values, id=row_id, revision=revision)
        if kind == "lines":
            row["source"] = old.source if old else source
            if declarations is not None:
                old_key = rules.active_declaration_key(old.data) if old else None
                if old_key is not None:
                    declarations[old_key].pop(row_id, None)
                if key is not None:
                    declarations[key][row_id] = row
        else:
            index[number] = row
        store.log(conn, org, user, kind, row_id, edit.reason, old.data if old else None, row)
        return {"row": row, "issues": issues}

    @app.post("/api/org/{org}/data/{kind}")
    def save_rows(org: int, kind: str, edits: list[Edit], request: Request):
        if not 1 <= len(edits) <= 1000:
            fail("한 번에 1,000행까지 저장할 수 있습니다.")
        with db.begin() as conn:
            if kind == "lines":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            organization(conn, org)
            index = member_index(conn, org)
            declarations = declaration_index(store.records(conn, store.declarations, org)) if kind == "lines" else None
            return [save(conn, org, kind, edit, actor(request), index=index, declarations=declarations) for edit in edits]

    @app.delete("/api/org/{org}/data/lines/{row_id}")
    def delete_line(org: int, row_id: int, revision: int, reason: str, request: Request):
        if not rules.clean(reason):
            fail("삭제 사유를 입력하세요.")
        with db.begin() as conn:
            table = store.declarations
            row = conn.execute(table.select().where(table.c.id == row_id, table.c.org == org,
                               table.c.revision == revision, table.c.deleted == 0)).first()
            if row is None:
                fail("자료를 다시 불러오세요.", 409)
            updated = conn.execute(table.update().where(table.c.id == row_id, table.c.revision == revision)
                                   .values(deleted=1, revision=revision + 1))
            if updated.rowcount != 1:
                fail("자료를 다시 불러오세요.", 409)
            store.log(conn, org, actor(request), "lines", row_id, reason[:500], row.data, None)
        return {"ok": True}

    @app.delete("/api/org/{org}/data/members/{row_id}")
    def delete_member(org: int, row_id: int, revision: int, request: Request):
        table = store.memberships
        with db.begin() as conn:
            deleted = conn.execute(table.delete().where(
                table.c.org == org, table.c.id == row_id, table.c.revision == revision,
            ).returning(table.c.data)).first()
            if deleted is None:
                fail("회원사 명부가 변경되었습니다. 새로고침 후 다시 확인하세요.", 409)
            store.log(conn, org, actor(request), "members", row_id, "회원사 삭제", deleted.data, None)
        return {"ok": True}

    @app.post("/api/org/{org}/data/lines/reset")
    def reset_lines(org: int, reset: ResetLedger, request: Request):
        if reset.confirmation != "모두리셋":
            fail("확인 문구를 정확히 입력하세요.")
        table = store.declarations
        with db.begin() as conn:
            organization(conn, org)
            result = conn.execute(table.update().where(table.c.org == org, table.c.deleted == 0)
                                  .values(deleted=1, revision=table.c.revision + 1))
            if result.rowcount:
                store.log(conn, org, actor(request), "lines", 0, "원장 전체 초기화",
                          {"active_count": result.rowcount}, {"active_count": 0})
        return {"count": result.rowcount}

    @app.post("/api/org/{org}/import/{kind}")
    async def upload(org: int, kind: str, name: str, request: Request):
        from starlette.concurrency import run_in_threadpool

        table_for(kind)
        if not name.lower().endswith(".xlsx") or any(ch in name for ch in ("/", "\\", ":")) or len(name) > 240:
            fail("XLSX 파일명을 확인하세요.")
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > 20_000_000:
                fail("파일 크기는 20MB 이하여야 합니다.", 413)
            payload.extend(chunk)
        return await run_in_threadpool(import_book, org, kind, name, bytes(payload), actor(request))

    def import_book(org, kind, name, data, user):
        try:
            sheet_name, profile, rows = books.read_rows(data, FIELDS[kind])
        except (ValueError, BadZipFile, InvalidFileException, KeyError, OSError):
            fail("Excel 파일 구조를 확인하거나 표준 양식을 사용하세요.")
        sha = books.digest(data)
        with db.begin() as conn:
            if kind == "lines":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            organization(conn, org)
            table = store.import_files
            new = conn.execute(insert(table).values(org=org, sha=sha, kind=kind, name=name, data=data)
                               .on_conflict_do_nothing(index_elements=["org", "sha", "kind"]))
            if new.rowcount:
                file_id = new.inserted_primary_key[0]
            else:
                file_id = conn.execute(select(table.c.id).where(table.c.org == org, table.c.sha == sha,
                                                               table.c.kind == kind)).scalar_one()
            sources = store.import_rows
            previous_sources = {}
            if not new.rowcount and kind == "lines":
                lines = store.declarations
                active = conn.execute(select(lines.c.id).join(sources, lines.c.source == sources.c.id)
                                      .where(sources.c.file == file_id, lines.c.deleted == 0).limit(1)).first()
                if active:
                    fail("D081 같은 원본 파일을 이미 가져왔습니다.", 409)
            elif not new.rowcount:
                previous_sources = dict(conn.execute(select(sources.c.row, sources.c.id)
                                        .where(sources.c.file == file_id, sources.c.sheet == sheet_name)).tuples().all())
            index = member_index(conn, org)
            declarations = declaration_index(store.records(conn, store.declarations, org)) if kind == "lines" else None
            rejected = []
            imported = skipped = 0
            imported_ids, review_ids = set(), set()
            for row_index, original in rows:
                source = previous_sources.get(row_index)
                values = original
                if kind == "members":
                    if source is not None and rules.business_number(original.get("number")) in index:
                        skipped += 1
                        continue
                    values = dict(original)
                    member_kind = values.get("kind")
                    values["kind"] = {"생산": "생산자", "수출": "수출업체"}.get(member_kind, member_kind or "")
                    if values.get("email") == "개인우편":
                        values["email"] = ""
                if source is None:
                    source = conn.execute(sources.insert().values(file=file_id, sheet=sheet_name,
                                          row=row_index, original=original)).inserted_primary_key[0]
                result = save(conn, org, kind, Edit(values=values, reason=f"Excel 가져오기 ({source})"),
                              user, source, index, declarations)
                if "row" in result:
                    imported += 1
                    row_id = result["row"]["id"]
                    imported_ids.add(row_id)
                    for problem in result["issues"]:
                        if problem["severity"] != "INFO":
                            review_ids.update((row_id, problem.get("row_id", row_id)))
                else:
                    rejected.append(dict(row=row_index, issues=result["issues"]))
            if new.rowcount or kind == "lines" or imported:
                store.log(conn, org, user, "Excel", file_id, "원본 저장" if new.rowcount or kind == "lines" else "원본 재처리",
                          None, dict(name=name, sha=sha, rows=len(rows), imported=imported))
        summary = {"skipped": skipped} if kind == "members" else {"needs_review": len(imported_ids & review_ids)}
        return dict(imported=imported, rejected=rejected, profile=profile, **summary)

    ocr_path = "/api/org/{org}/ocr"

    def line_pdfs(conn, org):
        lines, sources, files = store.declarations, store.import_rows, store.import_files
        query = (select(lines.c.id, files.c.id.label("file"), files.c.name, sources.c.row.label("page"))
                 .select_from(lines.join(sources, lines.c.source == sources.c.id)
                              .join(files, files.c.id == sources.c.file))
                 .where(lines.c.org == org, files.c.org == org, files.c.kind == "pdf"))
        return {row["id"]: dict(file=row["file"], name=row["name"], page=row["page"])
                for row in conn.execute(query).mappings()}

    def ocr_file(conn, org, file_id):
        files = store.import_files
        record = conn.execute(files.select().where(files.c.id == file_id, files.c.org == org,
                                                   files.c.kind == "pdf")).mappings().first()
        if record is None:
            fail("PDF 접수 자료를 찾을 수 없습니다.", 404)
        return record

    def ocr_pages(conn, file_id):
        sources = store.import_rows
        return list(conn.execute(sources.select().where(sources.c.file == file_id)
                                 .order_by(sources.c.row)).mappings())

    def ocr_approved(conn, file_id):
        lines, sources = store.declarations, store.import_rows
        return conn.execute(select(lines.c.id).join(sources, lines.c.source == sources.c.id)
                            .where(sources.c.file == file_id).limit(1)).first() is not None

    def extract_ocr(org, name, data, user):
        sha = books.digest(data)
        files = store.import_files
        query = select(files.c.id).where(files.c.org == org, files.c.sha == sha, files.c.kind == "pdf")

        def cached(conn, file_id):
            originals = [row["original"] for row in ocr_pages(conn, file_id)]
            approved = ocr_approved(conn, file_id)
            if approved or (originals and all(page.get("extractor_version") == ocr.EXTRACTION_VERSION
                                              and page.get("profile_id") == profile_id for page in originals)):
                return dict(id=file_id, pages=originals, approved=approved)
            return None

        with db.connect() as conn:
            organization(conn, org)
            profiles = store.ocr_profiles
            profile = conn.execute(profiles.select().where(profiles.c.org == org, profiles.c.active == 1)).first()
            profile_id = profile.id if profile else None
            file_id = conn.execute(query).scalar()
            if file_id is not None and (previous := cached(conn, file_id)):
                return previous
        try:
            pages = ocr.extract(data)
            if profile:
                ocr_profiles.apply(data, pages, ocr_profiles.Profile.model_validate(profile.data))
                for page in pages:
                    page["profile_id"] = profile_id
        except ImportError:
            fail("PDF 처리 구성 요소를 설치한 뒤 다시 시도하세요.", 503)
        except ValueError as error:
            fail(str(error))
        with db.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            active_profile = conn.execute(select(profiles.c.id).where(profiles.c.org == org, profiles.c.active == 1)).scalar()
            if profile_id != active_profile:
                fail("OCR 템플릿이 변경됐습니다. PDF를 다시 올려 주세요.", 409)
            file_id = conn.execute(query).scalar()
            if file_id is not None:
                if previous := cached(conn, file_id):
                    return previous
                sources = store.import_rows
                for page in pages:
                    conn.execute(sources.update().where(sources.c.file == file_id, sources.c.row == page["page"])
                                 .values(original=page))
            else:
                file_id = conn.execute(files.insert().values(org=org, sha=sha, kind="pdf", name=name,
                                                            data=data)).inserted_primary_key[0]
                conn.execute(store.import_rows.insert(), [
                    dict(file=file_id, sheet="PDF", row=page["page"], original=page) for page in pages
                ])
                store.log(conn, org, user, "PDF", file_id, "OCR 원본 접수", None,
                          dict(name=name, sha=sha, pages=len(pages)))
            return dict(id=file_id, pages=[row["original"] for row in ocr_pages(conn, file_id)],
                        approved=ocr_approved(conn, file_id))

    @app.post(ocr_path)
    async def upload_ocr(org: int, name: str, request: Request):
        from starlette.concurrency import run_in_threadpool

        if not name.lower().endswith(".pdf") or len(name) > 240 or any(ch in name for ch in "/\\:"):
            fail("PDF 파일명을 확인하세요.")
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > ocr.MAX_BYTES:
                fail("PDF 파일 크기는 20MB 이하여야 합니다.", 413)
            payload.extend(chunk)
        return await run_in_threadpool(extract_ocr, org, name, bytes(payload), actor(request))

    @app.get(ocr_path + "/{file_id}/page/{page}")
    def preview_ocr_page(org: int, file_id: int, page: int, rotation: int = 0):
        if rotation not in {0, 90, 180, 270}:
            fail("회전 각도를 확인하세요.")
        with db.connect() as conn:
            data = ocr_file(conn, org, file_id)["data"]
        try:
            return Response(ocr.page_image(data, page, rotation=rotation), media_type="image/png")
        except ValueError as error:
            fail(str(error))

    @app.post(ocr_path + "/{file_id}/page/{page}/region")
    def recheck_ocr_region(org: int, file_id: int, page: int, row: int, field: str):
        with db.connect() as conn:
            data = ocr_file(conn, org, file_id)["data"]
            source = next((record["original"] for record in ocr_pages(conn, file_id) if record["row"] == page), {})
        evidence = source.get("evidence", [])
        location = evidence[row].get(field, {}) if 0 <= row < len(evidence) else {}
        box = location.get("box")
        if not box:
            fail("이 항목에 저장된 원본 영역이 없습니다.", 404)
        return ocr.recognize_region(data, location.get("page", page), box)

    @app.post(ocr_path + "/{file_id}/page/{page}/selection")
    def recheck_ocr_selection(org: int, file_id: int, page: int, selection: OcrSelection):
        x0, y0, x1, y1 = selection.box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and x1 - x0 >= .002 and y1 - y0 >= .002):
            fail("원본에서 인식할 영역을 다시 지정하세요.")
        with db.connect() as conn:
            data = ocr_file(conn, org, file_id)["data"]
        try:
            result = ocr.recognize_region(data, page, selection.box)
            return dict(result, candidates=ocr_profiles.selection_values(result["text"]) if not result["warning"] else {})
        except ValueError as error:
            fail(str(error))

    profile_table = store.ocr_profiles
    profile_path = ocr_path + "/profiles"

    @app.get(profile_path)
    def list_ocr_profiles(org: int):
        with db.connect() as conn:
            organization(conn, org)
            return [dict(row) for row in conn.execute(profile_table.select().where(profile_table.c.org == org)
                                                     .order_by(profile_table.c.id.desc())).mappings()]

    @app.post(profile_path)
    async def save_ocr_profile(org: int, request: Request):
        from pydantic import ValidationError
        from starlette.concurrency import run_in_threadpool

        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > 8192:
                fail("템플릿 JSON은 8KB 이하여야 합니다.", 413)
            payload.extend(chunk)
        try:
            profile = ocr_profiles.Profile.model_validate_json(payload)
        except ValidationError:
            fail("템플릿 이름, 지원 서식 및 필드 영역 JSON을 확인하세요.", 422)
        return await run_in_threadpool(persist_ocr_profile, org, profile, actor(request))

    def persist_ocr_profile(org, profile, user):
        with db.begin() as conn:
            organization(conn, org)
            version = conn.execute(profile_table.insert().values(
                org=org, data=profile.model_dump(mode="json"), validated=0, active=0)).inserted_primary_key[0]
            store.log(conn, org, user, "OCR 템플릿", version, "버전 저장", None, None)
        return {"id": version}

    @app.post(profile_path + "/{version}/preview")
    def preview_ocr_profile(org: int, version: int, file_id: int, page: int):
        key = (profile_table.c.org == org) & (profile_table.c.id == version)
        with db.connect() as conn:
            data = ocr_file(conn, org, file_id)["data"]
            config = conn.execute(select(profile_table.c.data).where(key)).scalar()
            stored = next((row["original"] for row in ocr_pages(conn, file_id) if row["row"] == page), None)
        if config is None:
            fail("템플릿 버전을 찾을 수 없습니다.", 404)
        if stored is None:
            fail("원본 쪽 번호를 확인하세요.")
        profile = ocr_profiles.Profile.model_validate(config)
        if profile.source == "scan":
            candidates = ocr_profiles.preview_scan(data, stored, profile)
        else:
            with ocr.PDF_LOCK, ocr.open_pdf(data) as document, closing(document[page - 1]) as pdf_page:
                _, rows = ocr.read_pdf_page(pdf_page)
                candidates = ocr_profiles.preview(pdf_page, profile) if len(rows) == 1 else {}
        passed = len(candidates) == len(profile.regions)
        if passed:
            with db.begin() as conn:
                conn.execute(profile_table.update().where(key).values(validated=1))
        return {"candidates": candidates, "passed": passed}

    @app.post(profile_path + "/{version}/activate")
    def activate_ocr_profile(org: int, version: int, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            organization(conn, org)
            group = profile_table.c.org == org
            chosen = group & (profile_table.c.id == version)
            if version and not conn.execute(select(profile_table.c.validated).where(chosen)).scalar():
                fail("추출 미리보기를 통과한 버전만 활성화할 수 있습니다.", 409)
            conn.execute(profile_table.update().where(group).values(active=0))
            if version:
                conn.execute(profile_table.update().where(chosen).values(active=1))
            store.log(conn, org, actor(request), "OCR 템플릿", version, "활성 버전 변경", None, None)
        return {"active": version}

    @app.get(ocr_path + "/{file_id}/original")
    def original_pdf(org: int, file_id: int):
        with db.connect() as conn:
            file = ocr_file(conn, org, file_id)
            if not ocr_approved(conn, file_id):
                fail("원장에 등록된 PDF를 찾을 수 없습니다.", 404)
        return Response(file["data"], media_type="application/pdf", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file["name"]),
            "Cache-Control": "no-store",
        })

    @app.get(ocr_path + "/{file_id}/pages")
    def stored_pdf_pages(org: int, file_id: int):
        with db.connect() as conn:
            sources, files = store.import_rows, store.import_files
            pages = conn.execute(select(func.count()).select_from(sources.join(files))
                                 .where(files.c.id == file_id, files.c.org == org,
                                        files.c.kind == "pdf")).scalar_one()
            if not pages:
                fail("PDF 접수 자료를 찾을 수 없습니다.", 404)
            return pages

    @app.post(ocr_path + "/{file_id}/approve")
    def approve_ocr(org: int, file_id: int, reviewed: list[OcrRow], request: Request, validate_only: bool = False):
        if not 1 <= len(reviewed) <= 300:
            fail("한 PDF에서 1~300개 품목란을 승인할 수 있습니다.")
        with db.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            file = ocr_file(conn, org, file_id)
            if ocr_approved(conn, file_id):
                fail("이 PDF는 이미 원장에 등록되었습니다.", 409)
            pages = {row["row"]: row["id"] for row in ocr_pages(conn, file_id)}
            index = member_index(conn, org)
            errors = []
            validated = []
            submitted = {}
            for position, row in enumerate(reviewed, 1):
                if row.page not in pages:
                    fail("PDF 원본 쪽을 확인하세요.")
                values = {key: rules.clean(value) for key, value in row.values.items() if key in FIELDS["lines"]}
                if any(len(value) > 4000 for value in values.values()):
                    fail("셀 입력값은 4,000자 이하여야 합니다.")
                problems = rules.validate_line(values, index)[0]
                key = rules.active_declaration_key(values)
                if key is not None:
                    identity = key, rules.declaration_version(values)
                    first = submitted.setdefault(identity, position)
                    if first != position:
                        problems.append(rules.issue("D080", "line", f"같은 신고번호·품목란·정정 버전이 검토 {first}행에 있습니다."))
                for key, confirmed in (("usd", row.verified_usd), ("kg", row.verified_kg)):
                    value = values.get(key, "")
                    if not value or rules.decimal(value) is None or value != rules.clean(confirmed):
                        problems.append(rules.issue("P040", key, "원본 PDF와 수치를 대조하고 확인하세요."))
                if values.get("fob") and rules.decimal(values["fob"]) != rules.decimal(values.get("usd")):
                    problems.append(rules.issue("P025", "fob", "FOB(USD)와 신고금액(USD)을 대조하세요."))
                errors.extend(dict(problem, position=position) for problem in problems if problem["severity"] == "ERROR")
                validated.append((row.page, values))
            if errors:
                return JSONResponse({"issues": errors}, status_code=422)
            if validate_only:
                return {"valid": True}
            user = actor(request)
            declarations = declaration_index(store.records(conn, store.declarations, org))
            imported_ids, review_ids = set(), set()
            for page, values in validated:
                result = save(conn, org, "lines", Edit(values=values, reason="OCR 원본 대조 후 승인"),
                              user, pages[page], index, declarations)
                row_id = result["row"]["id"]
                imported_ids.add(row_id)
                for problem in result["issues"]:
                    if problem["severity"] == "ERROR":
                        review_ids.update((row_id, problem.get("row_id", row_id)))
            store.log(conn, org, user, "PDF", file_id, "OCR 승인", None,
                      dict(sha=file["sha"], rows=len(validated), verified=["usd", "kg"]))
        return {"imported": len(validated), "needs_review": len(imported_ids & review_ids)}

    @app.get("/api/org/{org}/export/{kind}")
    def export_data(org: int, kind: str, template: bool = False):
        table = table_for(kind)
        with db.connect() as conn:
            name = organization(conn, org)
            rows = [] if template else store.records(conn, table, org)
            if kind == "lines" and not template:
                index = member_index(conn, org)
                agreements = contract_gate.context(conn, org)
                declarations = declaration_index(rows)
                rows = [
                    dict(row, issues="\n".join(
                        p["code"] + " " + p["message"]
                        for p in rules.validate_line(row, index)[0] + contract_gate.evaluate(row, index, agreements)[0] + declaration_issues(row, declarations, row["id"])
                    )) for row in rows
                ]
            fields = FIELDS[kind] | ({"source": "원본 행 ID", "issues": "검증 결과"} if kind == "lines" and not template else {})
        title = "회원사명부" if kind == "members" else "수출신고필증원장"
        member_template = kind == "members" and template
        filename = f"{title}_표준양식_{name}.xlsx" if member_template else f"{title}_{name}.xlsx"
        return download(books.workbook([(title, fields, rows)], member_template=member_template), filename)

    @app.get("/api/org/{org}/policies")
    def policies(org: int):
        with db.connect() as conn:
            return [r.data for r in conn.execute(store.policies.select().where(store.policies.c.org == org))]

    @app.put("/api/org/{org}/policies/{year}")
    def policy(org: int, year: int, policy: Policy, request: Request):
        import re

        cutoff = rules.day(policy.cutoff)
        hs = sorted({re.sub(r"[.\s-]", "", rules.clean(v)) for v in policy.hs})
        if (not cutoff or not 1900 <= year <= 2100 or policy.basis not in ("shipped", "declared")
                or any(not re.fullmatch(r"[0-9]{10}", v) for v in hs)):
            fail("기준일, 귀속연도 기준, HS코드 10자리를 확인하세요.")
        data = dict(year=year, cutoff=cutoff.isoformat(), basis=policy.basis, hs=hs)
        with db.begin() as conn:
            organization(conn, org)
            table = store.policies
            previous = conn.execute(select(table.c.data).where(table.c.org == org, table.c.year == year)).scalar()
            conn.execute(insert(table).values(org=org, year=year, data=data)
                         .on_conflict_do_update(index_elements=["org", "year"], set_={"data": data}))
            store.log(conn, org, actor(request), "계산 정책", year, policy.reason, previous, data)
        return data

    def save_calculation(conn, org, name, policy, rows, members, request):
        year = policy["year"]
        result = contract_gate.calculate(conn, org, rows, members, policy)
        result.update(rule=rules.VERSION, policy=policy, organization=name, rows=rows, members=members)
        stamp = store.now()
        run_id = conn.execute(store.runs.insert().values(org=org, year=year, time=stamp,
                              data=result)).inserted_primary_key[0]
        store.log(conn, org, actor(request), "계산 실행", run_id, "계산", None, {"year": year, "rule": rules.VERSION})
        return run_id, stamp, result

    @app.post("/api/org/{org}/runs")
    def calculate(org: int, year: int, request: Request):
        with db.begin() as conn:
            name = organization(conn, org)
            table = store.policies
            policy = conn.execute(select(table.c.data).where(table.c.org == org, table.c.year == year)).scalar()
            if not policy:
                fail("설정에서 귀속연도, 회원자격 기준일, 대상 HS코드를 등록하세요.")
            rows = store.records(conn, store.declarations, org)
            members = store.records(conn, store.memberships, org)
            run_id, stamp, result = save_calculation(conn, org, name, policy, rows, members, request)
        return dict(id=run_id, time=stamp, ready=result["ready"])

    @app.get("/api/org/{org}/runs")
    def list_runs(org: int):
        table = store.runs
        with db.connect() as conn:
            query = select(table.c.id, table.c.year, table.c.time).where(table.c.org == org).order_by(table.c.id.desc())
            return [dict(r) for r in conn.execute(query).mappings()]

    def get_run(conn, org, run_id):
        table = store.runs
        run = conn.execute(table.select().where(table.c.org == org, table.c.id == run_id)).first()
        if run is None:
            fail("계산 실행번호를 확인하세요.", 404)
        return run.data

    @app.get("/api/org/{org}/runs/{run_id}")
    def run_detail(org: int, run_id: int):
        with db.connect() as conn:
            run = get_run(conn, org, run_id)
            fields = ("rule", "policy", "checks", "member_issues", "actual_usd", "actual_kg", "accepted_lines", "ledger", "ready")
            pdfs = line_pdfs(conn, org)
            ledger = [dict(row, pdf=next((pdfs[i] for i in row.get("source_ids", [row["row_id"]]) if i in pdfs), None))
                      for row in run["ledger"]]
            return dict({k: run[k] for k in fields} | {"ledger": ledger}, summary=rules.summarize(run["ledger"]),
                        attributed_usd=str(sum((rules.Decimal(row["usd"]) for row in run["ledger"]), rules.Decimal(0))))

    @app.get("/api/org/{org}/audit")
    def history(org: int, row_id: int | None = None):
        table = store.audit
        query = table.select().where(table.c.org == org).order_by(table.c.id.desc()).limit(500)
        if row_id is not None:
            query = query.where(table.c.entity == "lines", table.c.target == row_id)
        with db.connect() as conn:
            records = [dict(r) for r in conn.execute(query).mappings()]
        # Display older system-generated labels with the current terminology.
        for record in records:
            if record["entity"] == "산정 정책":
                record["entity"] = "계산 정책"
            elif record["entity"] == "산정 실행":
                record["entity"] = "계산 실행"
                if record["reason"] == "산정":
                    record["reason"] = "계산"
        return records

    @app.get("/api/org/{org}/runs/{run_id}/evidence/{row_id}")
    def evidence(org: int, run_id: int, row_id: int):
        with db.connect() as conn:
            run = get_run(conn, org, run_id)
            row = next((r for r in run["rows"] if r["id"] == row_id), None)
            if not row:
                fail("원장 자료를 찾을 수 없습니다.", 404)
            sources, files = store.import_rows, store.import_files
            source = conn.execute(select(sources.c.sheet, sources.c.row, sources.c.original, files.c.name, files.c.sha)
                                  .join(files, files.c.id == sources.c.file).where(sources.c.id == row["source"])).mappings().first()
        return dict(row=row, source=dict(source) if source else None, decisions=[r for r in run["decisions"] if r["row_id"] == row_id])

    @app.get("/api/org/{org}/runs/{run_id}/report")
    def report(org: int, run_id: int, bundle: bool = False):
        with db.connect() as conn:
            run = get_run(conn, org, run_id)
            src, files = store.import_rows, store.import_files
            ids = [row["source"] for row in run["rows"] if row["source"]]
            hashes = [dict(item) for offset in range(0, len(ids), 500) for item in conn.execute(
                select(src.c.id, src.c.sheet, src.c.row, files.c.name, files.c.sha).join(files, files.c.id == src.c.file)
                .where(src.c.id.in_(ids[offset:offset + 500]))).mappings()]
        ledger = [dict(r, source_ids=", ".join(map(str, r["source_ids"])), usd=rules.Decimal(r["usd"]),
                       kg=rules.Decimal(r["kg"]) if r["kg"] is not None else None) for r in run["decisions"]]
        state = "검증 통과 · 검토용" if run["ready"] else "검증 미완료"
        sheets = [
            ("계산 정보", dict(id="실행번호", rule="규칙", cutoff="기준일", state="상태", usd="실제 USD", kg="실제 kg"),
             [dict(id=run_id, rule=run["rule"], cutoff=run["policy"]["cutoff"], state=state,
                   usd=rules.Decimal(run["actual_usd"]),
                   kg=rules.Decimal(run["actual_kg"]) if run["actual_kg"] is not None else None)]),
            ("회원사별 집계", SUMMARY, rules.summarize(run["ledger"])),
            ("인정 및 제외 근거", LEDGER, ledger),
            ("검증 내역", CHECKS, run["checks"] + run["member_issues"]),
            ("원장 및 출처", books.LINE | dict(id="원장 ID", source="원본 행 ID"), run["rows"]),
            ("원본 해시", dict(id="원본 행 ID", sheet="시트", row="행", name="파일", sha="SHA-256"), hashes),
        ]
        data = books.workbook(sheets)
        if not bundle:
            return download(data, f"계산 검증_{run_id}.xlsx")
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr(f"계산 검증_{run_id}.xlsx", data)
            archive.writestr("계산정책.json", json.dumps(run["policy"], ensure_ascii=False, indent=2))
        return download(stream.getvalue(), f"감사증빙_{run_id}.zip", "application/zip")

    @app.get("/api/backup")
    def backup():
        import sqlite3
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp:
            target = Path(temp) / "epms.db"
            with db.connect() as conn:
                dest = sqlite3.connect(target)
                try:
                    conn.connection.driver_connection.backup(dest)
                finally:
                    dest.close()
            return download(target.read_bytes(), "공동정산 수출실적 관리_백업.db", "application/octet-stream")

    def final_blocker(conn, org, run, annual=False):
        rows = store.records(conn, store.declarations, org)
        members = store.records(conn, store.memberships, org)
        gate = contract_gate.validation(conn, org, rows, members, run['policy'])
        if gate['checks']:
            return gate['checks'][0]['message']
        if run.get('contract_validation') != gate:
            return '계약 확인 결과가 변경되었습니다. 계약서 등록을 확인하고 현재 자료로 다시 검토하세요.'
        if run.get("rule") != rules.VERSION:
            return "계산 규칙이 변경되었습니다. 다시 계산하세요."
        if not run["ready"] or (not annual and not run["accepted_lines"]):
            return "검증을 완료하고 인정 대상 품목란을 확인하세요."
        table = store.policies
        policy = conn.execute(select(table.c.data).where(table.c.org == org,
                              table.c.year == run["policy"]["year"])).scalar()
        rows = store.records(conn, store.declarations, org)
        members = store.records(conn, store.memberships, org)
        if run["members"] != members or run["policy"] != policy:
            return "원장, 명부 또는 정책이 변경되었습니다. 다시 계산하세요."
        if run["rows"] != rows:
            current = contract_gate.calculate(conn, org, rows, members, policy) if annual else None
            if current is None or any(current[key] != run[key] for key in current):
                return "원장, 명부 또는 정책이 변경되었습니다. 다시 계산하세요."
        if not annual and policy["cutoff"] != submission.CONFIG["cutoff"]:
            return "제출양식의 회원 기준일은 2026-12-31입니다."
        return None

    @app.api_route("/api/org/{org}/submission-review", methods=["GET", "POST"])
    def submission_review(org: int, year: int, request: Request):
        run_table, snapshot_table, policies = store.runs, store.submissions, store.policies
        years = (year - 1, year)
        with db.begin() as conn:
            name = organization(conn, org)
            rules_by_year = dict(conn.execute(select(policies.c.year, policies.c.data).where(
                policies.c.org == org, policies.c.year.in_(years))).all())
            missing = [value for value in years if value not in rules_by_year]
            if missing and request.method == "POST":
                fail("두 실적연도의 계산 정책을 등록하세요.")
            rows = store.records(conn, store.declarations, org)
            members = store.records(conn, store.memberships, org)
            row_names = {r["id"]: r.get("number") for r in rows}
            member_names = {m["id"]: m.get("name") for m in members}
            results = []
            for value in years:
                policy = rules_by_year.get(value)
                latest = conn.execute(select(run_table.c.id, run_table.c.data).where(
                    run_table.c.org == org, run_table.c.year == value).order_by(run_table.c.id.desc()).limit(1)).first()
                run_id, result = latest if latest else (None, None)
                gate = contract_gate.validation(conn, org, rows, members, policy) if policy else None
                current = bool(result and result.get('contract_validation') == gate and result["rule"] == rules.VERSION and result["organization"] == name
                               and result["policy"] == policy and result["rows"] == rows and result["members"] == members)
                if request.method == "POST" and not current:
                    run_id, _, result = save_calculation(conn, org, name, policy, rows, members, request)
                    current = True
                if current:
                    checks = [dict(p, subject=row_names.get(p["row_id"])) for p in result["checks"] if p["severity"] != "INFO"]
                    checks.extend(dict(p, subject=member_names.get(p["member_id"])) for p in result["member_issues"])
                    results.append(dict(year=value, id=run_id, cutoff=policy["cutoff"], ready=result["ready"],
                                        checks=checks, summary=rules.summarize(result["ledger"]),
                                        actual_usd=result["actual_usd"], actual_kg=result["actual_kg"],
                                        attributed_usd=sum((rules.Decimal(r["usd"]) for r in result["ledger"]), rules.Decimal(0))))
            history = [dict(r) for r in conn.execute(select(snapshot_table.c.first_run, snapshot_table.c.second_run,
                run_table.c.time).join(run_table, snapshot_table.c.second_run == run_table.c.id).where(
                snapshot_table.c.org == org, snapshot_table.c.sheet == "annual-v2", run_table.c.year == year)
                .order_by(snapshot_table.c.id.desc())).mappings()]
            saved = len(results) == 2 and any(
                (item["first_run"], item["second_run"]) == (results[0]["id"], results[1]["id"]) for item in history)
            return dict(missing=missing, results=results, history=history, saved=saved)

    @app.api_route("/api/org/{org}/annual-submission", methods=["GET", "POST"])
    def annual_submission(org: int, run_id: int, previous_id: int, request: Request, final: bool = False,
                          require_current: bool = False):
        table = store.submissions
        key = "annual-v2"
        with db.begin() as conn:
            if request.method == 'POST' and final:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            item = organization(conn, org)
            run = get_run(conn, org, run_id)
            year = run["policy"]["year"]
            previous = get_run(conn, org, previous_id)
            if previous["policy"]["year"] != year - 1:
                fail("선택 연도의 직전 연도 계산 결과를 선택하세요.")
            existing = conn.execute(select(table.c.data).where(table.c.org == org, table.c.first_run == previous_id,
                                    table.c.second_run == run_id, table.c.sheet == key)).scalar()
            blocker = None
            if require_current:
                rows = store.records(conn, store.declarations, org)
                members = store.records(conn, store.memberships, org)
                policies = store.policies
                for result in (previous, run):
                    policy = conn.execute(select(policies.c.data).where(
                        policies.c.org == org, policies.c.year == result["policy"]["year"])).scalar()
                    gate = contract_gate.validation(conn, org, rows, members, result['policy'])
                    if result.get('contract_validation') != gate:
                        fail(gate['checks'][0]['message'] if gate['checks'] else '계약 확인 결과가 변경되었습니다. 계약서 등록을 확인하고 현재 자료로 다시 검토하세요.', 409)
                    if (result["rows"] != rows or result["members"] != members or result["policy"] != policy
                            or result["organization"] != item or result["rule"] != rules.VERSION):
                        fail("확정 이후 자료가 변경되었거나 검토 결과가 오래되었습니다. 현재 자료를 다시 검토하세요.", 409)
            if not existing:
                for result in (previous, run):
                    reason = final_blocker(conn, org, result, annual=True)
                    if reason:
                        blocker = f"{result['policy']['year']}년 · {reason}"
                        break
            if request.method == "GET":
                return dict(year=year, cutoff=run["policy"]["cutoff"], previous_cutoff=previous["policy"]["cutoff"],
                            saved=bool(existing), blocker=blocker)
            filename = f"공동정산_{item}_{year}년_{'최종확정본' if final else '검증미완료'}.xlsx"
            if final and existing:
                return download(existing, filename)
            if final and blocker:
                fail(blocker)
            template = next((ROOT / "samples").glob("2027*.xlsx"), None)
            if not template:
                fail("제출자료 기준 서식 파일을 찾을 수 없습니다.")
            try:
                data = submission.generate(template, previous, run, submission.CONFIG["sheets"][0],
                                           "최종 확정본" if final else "검증 미완료 · 초안",
                                           (previous_id, run_id), item=item)
            except ValueError as exc:
                fail(str(exc))
            if final:
                result = conn.execute(table.insert().values(org=org, first_run=previous_id, second_run=run_id, sheet=key, data=data))
                store.log(conn, org, actor(request), "최종 확정", result.inserted_primary_key[0],
                          "연도별 제출자료 확정", None, {"year": year, "run": run_id, "previous_run": previous_id})
            return download(data, filename)

    @app.get("/api/org/{org}/submission-readiness")
    def submission_readiness(org: int, first_id: int | None = None, second_id: int | None = None,
                             sheet: str | None = None):
        with db.connect() as conn:
            organization(conn, org)
            table = store.submissions
            saved = conn.execute(select(table.c.id).where(table.c.org == org, table.c.first_run == first_id,
                                 table.c.second_run == second_id, table.c.sheet == sheet)).scalar()
            output = {}
            for year, run_id in ((2025, first_id), (2026, second_id)):
                if run_id is None:
                    continue
                run = get_run(conn, org, run_id)
                if run["policy"]["year"] != year:
                    fail("연도에 맞는 계산 실행번호를 선택하세요.")
                output[year] = dict(cutoff=run["policy"]["cutoff"], ready=run["ready"],
                                    blocker=None if saved else final_blocker(conn, org, run))
            return dict(years=output, saved=bool(saved))

    @app.post("/api/org/{org}/submission")
    def submit(org: int, first_id: int, second_id: int, sheet: str, request: Request, final: bool = False):
        table = store.submissions
        with db.begin() as conn:
            if final:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            name = organization(conn, org)
            first, second = (get_run(conn, org, i) for i in (first_id, second_id))
            if (first["policy"]["year"], second["policy"]["year"]) != (2025, 2026):
                fail("2025년과 2026년 계산 실행번호를 순서대로 선택하세요.")
            if final:
                existing = conn.execute(select(table.c.data).where(table.c.org == org, table.c.first_run == first_id,
                                        table.c.second_run == second_id, table.c.sheet == sheet)).scalar()
                if existing is not None:
                    return download(existing, f"공동정산_{name}_최종확정본.xlsx")
                rows = store.records(conn, store.declarations, org)
                members = store.records(conn, store.memberships, org)
                for run in (first, second):
                    gate = contract_gate.validation(conn, org, rows, members, run['policy'])
                    if gate['checks']:
                        fail(gate['checks'][0]['message'])
                for run in (first, second):
                    blocker = final_blocker(conn, org, run)
                    if blocker:
                        fail(blocker)
            template = next((ROOT / "samples").glob("2027*.xlsx"), None)
            if not template:
                fail("2027년 공식 양식 파일을 찾을 수 없습니다.")
            status = "최종 확정본" if final else "검증 미완료 · 초안"
            try:
                data = submission.generate(template, first, second, sheet, status, (first_id, second_id))
            except ValueError as exc:
                fail(str(exc))
            if final:
                result = conn.execute(table.insert().values(org=org, first_run=first_id, second_run=second_id,
                                      sheet=sheet, data=data))
                store.log(conn, org, actor(request), "최종 확정", result.inserted_primary_key[0],
                          "2027년 제출양식 확정", None, {"first": first_id, "second": second_id})
            return download(data, f"공동정산_{name}_{'최종확정본' if final else '검증미완료'}.xlsx")

    from epms.sorting_shared_api import install as install_sorting
    install_sorting(app, db, organization, actor)

    from epms.settlement_api import install as install_settlement
    install_settlement(app, db, organization, actor)

    from epms.member_contracts import install as install_contracts
    install_contracts(app, db, organization, actor)

    license_root = (Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT) / "licenses"
    if license_root.is_dir():
        app.mount("/oss", StaticFiles(directory=license_root, html=True), name="licenses")
    assets = ROOT / "dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    return app
