import json
from copy import copy
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string

from epms import books, rules

CONFIG = json.loads(Path(__file__).with_name("submission_2027.json").read_text(encoding="utf-8"))


def clear_example_rows(book):
    start, end = CONFIG["first_row"], CONFIG["last_row"]
    for name in CONFIG["sheets"]:
        ws = book[name]
        for cells in ws.iter_rows(min_row=start, max_row=end, min_col=2, max_col=17):
            for cell in cells:
                cell.value = None


def generate(template, first, second, sheet_name, status, run_ids, *, item=None):
    if sheet_name not in CONFIG["sheets"]:
        raise ValueError("작성할 공식 양식 시트를 선택하세요.")
    cutoff = rules.day(CONFIG["cutoff"])
    if item is None:
        members = [m for m in second["members"] if rules.eligible(m, cutoff)]
    else:
        roster = {}
        for run in (first, second):
            cutoff = rules.day(run["policy"]["cutoff"])
            roster.update((rules.business_number(m["number"]), m)
                          for m in run["members"] if rules.eligible(m, cutoff))
        members = list(roster.values())
    members.sort(key=lambda row: (row["name"], row["number"]))
    summaries = [{r["number"]: r for r in rules.summarize(run["ledger"])} for run in (first, second)]
    amounts_missing = (set(summaries[0]) | set(summaries[1])) - {rules.business_number(m["number"]) for m in members}
    if amounts_missing:
        raise ValueError("실적 귀속 회원과 제출 기준일 명부에 차이가 있습니다.")
    book = load_workbook(template, keep_links=False)
    if set(CONFIG["sheets"]) - set(book.sheetnames):
        raise ValueError("2027년 공식 양식의 시트 구성을 확인하세요.")
    start, end = CONFIG["first_row"], CONFIG["last_row"]
    clear_example_rows(book)
    ws = book[sheet_name]
    if item is not None:
        for name in CONFIG["sheets"]:
            if name != sheet_name:
                book.remove(book[name])
        title = "".join(c if c not in '[]:*?/\\' else ' ' for c in item).strip().strip("'")[:26]
        ws.title = f"{title or '품목'} {second['policy']['year']}"
        books.write_value(ws["L5"], f"{first['policy']['year']}년 수출 실적")
        books.write_value(ws["O5"], f"{second['policy']['year']}년 수출 실적")
        books.write_value(ws["B3"], "작성대상  각 실적연도 회원자격 기준일에 해당하는 회원사")
        ws.print_area = f"B2:Q{max(end, start + len(members) - 1)}"
    for row_index, member in enumerate(members, start):
        if row_index > end:
            ws.row_dimensions[row_index].height = ws.row_dimensions[end].height
            for col in range(2, 18):
                ws.cell(row_index, col)._style = copy(ws.cell(end, col)._style)
        member = dict(member, serial=row_index - start + 1)
        for key, col in CONFIG["member_columns"].items():
            books.write_value(ws[f"{col}{row_index}"], member.get(key))
        for columns, summary in zip(CONFIG["amount_columns"].values(), summaries):
            amounts = summary.get(rules.business_number(member["number"]), {})
            for col, key in zip(columns, ("direct_usd", "indirect_usd", "total_usd")):
                ws[f"{col}{row_index}"] = amounts.get(key, Decimal(0))
    for columns, summary in zip(CONFIG["amount_columns"].values(), summaries):
        for col, key in zip(columns, ("direct_usd", "indirect_usd", "total_usd")):
            actual = sum(ws.cell(i, column_index_from_string(col)).value for i in range(start, start + len(members)))
            expected = sum(item[key] for item in summary.values())
            if actual != expected:
                raise ValueError("제출자료의 합계 대사에 실패했습니다.")
    evidence = book.create_sheet("계산 및 검증 정보")
    evidence.append(["문서 상태", status])
    if item is None:
        evidence.append(["회원자격 기준일", CONFIG["cutoff"]])
    evidence.append(["제출양식 매핑", CONFIG["version"]])
    evidence.append(["실적연도", "계산 실행번호", "규칙", "회원자격 기준일"])
    for run_id, run in zip(run_ids, (first, second)):
        evidence.append([run["policy"]["year"], run_id, run["rule"], run["policy"]["cutoff"]])
    # Keep the supplied header and merged regions. The document status is visible on the worksheet.
    books.write_value(ws["B2"], status)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
