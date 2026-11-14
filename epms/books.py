"""Offline XLSX parsing, evidence capture, and workbook exports."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.cell import column_index_from_string

MEMBER = dict(name="회원사명", number="사업자등록번호", kind="회원사 유형", applied="가입신청일",
              approved="가입승인일", withdrawn="탈퇴일", aliases="업체명 별칭", address="주소",
              representative="대표자", contact="담당자", phone="연락처", email="이메일",
              corporate="법인등록번호", note="비고")
LINE = dict(number="신고번호", line="실제 품목란번호", version="정정 버전", declared="신고일",
            shipped="선적일", accepted="신고수리일", country="수출국", hs="HS코드", product="품명",
            usd="신고금액(USD)", fob="FOB(USD)", kg="순중량(kg)", quantity="수량", unit="수량단위",
            scope="공동정산 대상", status="신고 상태")
for key, label in (("agent", "수출대행자"), ("owner", "수출화주"), ("maker", "제조자")):
    LINE.update({key + "_name": label, key: label + " 사업자번호", key + "_state": label + " 확인"})

COMMON = dict(number="A", agent_name="D", owner_name="E", maker_name="F", hs="I", kg="J",
              usd="K", shipped="N", declared="O")
PROFILES = {
    "공통": (1, COMMON, {"A": "수출신고필증", "I": "HS코드"}),
    "배": (1, dict(owner_name="A", maker_name="B", declared="C", shipped="D",
                   number="F", product="G", country="H", fob="K"), {"F": "신고번호", "K": "FOB($)"}),
    "토마토": (2, dict(owner_name="C", maker_name="D", number="G", hs="I",
                     product="J", fob="N", country="P"), {"G": "면장번호", "I": "HS코드"}),
    "파프리카": (2, dict(number="D", fob="P"), {"D": "면장번호", "P": "금액(FOB$)"}),
}


def cell_text(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    return "" if value is None else str(value).strip()


def read_rows(data, fields):
    if len(data) > 20_000_000:
        raise ValueError("파일 크기는 20MB 이하여야 합니다.")
    with ZipFile(BytesIO(data)) as archive:
        if (sum(item.file_size for item in archive.infolist()) > 180_000_000
                or len(archive.infolist()) > 5000
                or any(name.lower().endswith("vbaproject.bin") for name in archive.namelist())):
            raise ValueError("파일의 압축 해제 크기 또는 매크로 포함 여부를 확인하세요.")
    book = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        ws = book.worksheets[0]
        ws.reset_dimensions()
        rows = ws.iter_rows(values_only=True, max_col=128)
        first = [cell_text(value) for value in next(rows, ())]
        columns = {key: first.index(label) for key, label in fields.items() if label in first}
        header, profile = 1, "표준"
        required = {"name", "number"} if fields is MEMBER else {"line", "number"}
        if fields is LINE and not required <= columns.keys():
            for candidate, (start, labels, signature) in PROFILES.items():
                if all(first[column_index_from_string(col) - 1] == value for col, value in signature.items()):
                    header, profile = start, candidate
                    columns = {key: column_index_from_string(col) - 1 for key, col in labels.items()}
                    break
        if profile == "표준" and not required <= columns.keys():
            raise ValueError("머리글을 찾을 수 없습니다. 표준 양식을 사용하세요.")
        for _ in range(header - 1):
            next(rows, None)
        result = []
        for i, values in enumerate(rows, header + 1):
            if i > 50_100:
                raise ValueError("파일을 50,000행 이하로 나누어 주세요.")
            row = {key: cell_text(values[col]) for key, col in columns.items()}
            if not any(row.values()) or any(v.replace(" ", "") in ("합계", "소계", "총계") for v in row.values()):
                continue
            result.append((i, row))
        if not result:
            raise ValueError("가져올 데이터가 없습니다.")
        return ws.title, profile, result
    finally:
        book.close()


def write_value(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def workbook(sheets):
    book = Workbook()
    book.remove(book.active)
    for title, fields, rows in sheets:
        ws = book.create_sheet(title)
        ws.append(list(fields.values()))
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="173458")
            cell.font = Font(color="FFFFFF", bold=True)
            ws.column_dimensions[cell.column_letter].width = max(17, len(str(cell.value)) * 2)
        for row_index, row in enumerate(rows, 2):
            for col, key in enumerate(fields, 1):
                value = row.get(key)
                cell = ws.cell(row_index, col)
                write_value(cell, value)
                if isinstance(value, Decimal):
                    cell.number_format = "#,##0.############"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def digest(data):
    return sha256(data).hexdigest()
