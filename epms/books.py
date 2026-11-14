"""Offline XLSX parsing, evidence capture, and workbook exports."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

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


def workbook(sheets, *, member_template=False):
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
    if member_template:
        add_member_template_controls(book)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def add_member_template_controls(book):
    ws = book.worksheets[0]
    last_row = 50_001
    required = ("name", "number", "kind", "approved")
    columns = {key: get_column_letter(i) for i, key in enumerate(MEMBER, 1)}
    for key, column in columns.items():
        ws.column_dimensions[column].number_format = "yyyy-mm-dd" if key in ("applied", "approved", "withdrawn") else "@"
        if key in required:
            ws[f"{column}1"].fill = PatternFill("solid", fgColor="17605B")
            ws[f"{column}1"].comment = Comment("필수 입력 항목입니다.", "EPMS")

    def validate(key, formula, message, *, validation_type="custom", title=None):
        label = MEMBER[key]
        rule = DataValidation(type=validation_type, formula1=formula, allow_blank=key not in required,
                              showErrorMessage=True, errorStyle="stop", errorTitle=label, error=message,
                              showInputMessage=True, promptTitle=label, prompt=title or message)
        ws.add_data_validation(rule)
        column = columns[key]
        rule.add(f"{column}2:{column}{last_row}")

    validate("name", "AND(ISTEXT(A2),LEN(TRIM(A2))>0,LEN(A2)<=4000)", "회원사명을 입력하세요. 최대 4,000자입니다.")
    number = 'SUBSTITUTE(B2,"-","")'
    checksum = f'SUMPRODUCT(--MID({number},ROW($1:$10),1),{{1;3;7;1;3;7;1;3;5;1}})'
    validate("number", f'IFERROR(AND(LEN({number})=10,TEXT(--{number},"0000000000")={number},'
             f'MOD({checksum}+INT(MID({number},9,1)/2),10)=0),FALSE)',
             "사업자등록번호 10자리와 마지막 검증 숫자를 확인하세요.",
             title="앞자리 0을 포함해 원본의 10자리를 입력하세요. 하이픈도 사용할 수 있습니다.")
    validate("kind", '"생산자,수출업체,복수"', "드롭다운에서 생산자, 수출업체, 복수 중 하나를 선택하세요.",
             validation_type="list")

    def valid_date(cell):
        return f"AND(ISNUMBER({cell}),{cell}=INT({cell}),{cell}>=1,{cell}<>60,{cell}<=DATE(9999,12,31))"

    validate("applied", f'IFERROR(AND({valid_date("D2")},OR(E2="",D2<=E2)),FALSE)',
             "가입신청일을 YYYY-MM-DD로 입력하세요. 가입승인일보다 늦을 수 없습니다.")
    validate("approved", f'IFERROR(AND({valid_date("E2")},OR(D2="",E2>=D2),OR(F2="",E2<=F2)),FALSE)',
             "가입승인일을 YYYY-MM-DD로 입력하세요. 가입신청일과 탈퇴일의 순서를 확인하세요.")
    validate("withdrawn", f'IFERROR(AND({valid_date("F2")},OR(E2="",F2>=E2)),FALSE)',
             "탈퇴일을 YYYY-MM-DD로 입력하세요. 가입승인일보다 빠를 수 없습니다.")

    validate("phone", 'AND(LEN(K2)>=7,LEN(K2)<=24,'
             'SUMPRODUCT(--ISERROR(FIND(MID(K2,ROW($1:$24),1),"+0123456789() -")))=0)',
             "전화번호는 7~24자입니다. 숫자, +, 괄호, 공백, 하이픈을 사용할 수 있습니다.")
    validate("email", 'IFERROR(AND(FIND("@",L2)>1,LEN(L2)-LEN(SUBSTITUTE(L2,"@",""))=1,'
             'FIND(".",L2,FIND("@",L2)+2)<LEN(L2),ISERROR(FIND(" ",L2)),CLEAN(L2)=L2,LEN(L2)<=4000),FALSE)',
             "이메일은 name@example.com 형식으로 입력하세요. 주소가 없으면 비워 두세요.")
    for key in ("aliases", "address", "representative", "contact", "corporate", "note"):
        cell = f"{columns[key]}2"
        validate(key, f"AND(ISTEXT({cell}),LEN({cell})<=4000)", "텍스트로 입력하세요. 최대 4,000자입니다.")

    guide = book.create_sheet("작성·AI 변환 안내")
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 100
    guide.append(["회원사 명부 작성", "녹색 머리글은 필수 입력 항목입니다."])
    guide.append(["직접 작성", "첫 번째 ‘회원사명부’ 시트 2행부터 업체당 한 행씩 작성하세요. 유형은 드롭다운에서 선택합니다."])
    guide.append(["업로드 전 확인", "붙여넣기와 AI가 생성한 파일은 Excel 입력 검사를 우회할 수 있습니다. 아래 검토 목록을 확인한 뒤 올리세요. 서버에서 검증 결과를 행별로 표시합니다."])
    guide.append(["AI에 전달할 요청", "아래 지침을 원본 명부와 이 표준양식에 함께 첨부해 AI에 전달하세요."])
    instructions = [
        ("출력 파일", "첨부한 표준양식을 복사해 .xlsx로 반환해 주세요. 첫 번째 시트 ‘회원사명부’의 1행 머리글과 열 순서를 그대로 유지하고 2행부터 값만 채워 주세요. 양식의 입력 검증과 안내 시트를 유지해 주세요."),
        ("한 업체에 한 행", "회원사명, 사업자등록번호, 회원사 유형, 가입승인일은 필수입니다. 합계, 소계, 제목, 설명 행을 빼고 동일 사업자등록번호는 한 번만 입력해 주세요. 정보가 충돌하면 원본 행 번호와 함께 알려 주세요."),
        ("사업자등록번호", "앞자리 0을 보존하도록 숫자 10자리를 텍스트로 입력해 주세요. 하이픈은 제거해도 됩니다. 검증 숫자가 맞는지 확인하고 원본 번호를 추측하거나 고치지 마세요. 법인등록번호는 별도 열에 텍스트로 입력해 주세요."),
        ("회원사 유형", "생산 또는 생산자는 ‘생산자’, 수출 또는 수출업체는 ‘수출업체’, 두 역할이 원본에 확인되면 ‘복수’를 입력해 주세요. 판단할 근거가 없으면 검토 목록에 적어 주세요."),
        ("날짜", "가입신청일, 가입승인일, 탈퇴일은 YYYY-MM-DD로 표시해 주세요. 가입신청일 ≤ 가입승인일 ≤ 탈퇴일 순서를 확인하고, 확인할 수 없는 날짜를 만들어 넣지 마세요. 가입신청일과 탈퇴일은 선택입니다."),
        ("연락처와 이메일", "전화번호는 앞자리 0을 보존하도록 텍스트로 입력해 주세요. 전화번호는 숫자, +, 괄호, 공백, 하이픈을 사용해 7~24자로 작성해 주세요. 이메일 열에는 실제 이메일 주소만 넣고 ‘개인우편’, ‘없음’, ‘-’ 등은 비워 주세요."),
        ("별칭과 선택 항목", "업체명 별칭이 여러 개면 |로 구분해 주세요. 주소, 대표자, 담당자, 연락처, 이메일, 법인등록번호, 비고 등 선택 항목의 원본 값이 없으면 비워 주세요."),
        ("검토 목록", "필수값 누락, 중복, 형식 오류, 판단이 필요한 행은 원본 행 번호와 이유를 별도 답변으로 알려 주세요. 확인이 필요한 행은 업로드용 시트에서 빼고 최종 입력 행 수를 알려 주세요. 한 파일에 최대 50,000행을 넣어 주세요."),
    ]
    for label, instruction in instructions:
        guide.append([label, instruction])
    for row in guide.iter_rows():
        heading = row[0].row in (1, 4)
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            if heading:
                cell.fill = PatternFill("solid", fgColor="173458")
                cell.font = Font(color="FFFFFF", bold=True)
        guide.row_dimensions[row[0].row].height = 28 if heading else 62


def digest(data):
    return sha256(data).hexdigest()
