"""Bounded XLSX preview preserving coordinates without evaluating formulas."""
from io import BytesIO
from zipfile import ZipFile
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from epms import sorting

HEADERS = dict(number=('신고번호', '수출신고필증번호'), quantity=('배분 순중량(kg)', '순중량(kg)', '수출신고필증상 기재된 수출물량 (순중량)(kg)'), product=('품목', '품명'), work_date=('공동선별기간', '작업일'), facility=('시설 ID', '공동선별장'), cost=('공동선별비용(원)',), line=('품목란', '품목란번호'), external_id=('작업 ID',))


def normalized(value):
    return ''.join(str(value or '').split())


def preview(data, sheet_name=None):
    if len(data) > 20_000_000:
        raise ValueError('파일은 20MB 이하여야 합니다.')
    with ZipFile(BytesIO(data)) as archive:
        if len(archive.infolist()) > 5000 or sum(i.file_size for i in archive.infolist()) > 180_000_000 or any(n.lower().endswith('vbaproject.bin') for n in archive.namelist()):
            raise ValueError('압축 크기 또는 매크로를 확인하세요.')
    book = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        sheets = book.sheetnames
        if sheet_name is None:
            return dict(sheets=sheets, rows=[], mapping={}, requires_sheet_selection=True)
        if sheet_name not in sheets or any(word in sheet_name for word in ('조정', '참고', '집계')):
            raise ValueError('공식 세부 시트를 선택하세요. 참고·조정·집계 시트는 제외합니다.')
        sheet = book[sheet_name]
        sheet.reset_dimensions()
        mapping, result, header = {}, [], None
        for index, row in enumerate(sheet.iter_rows(max_col=128), 1):
            if index > 50_050:
                raise ValueError('50,000행을 초과했습니다.')
            if header is None:
                if index > 50:
                    break
                found = {}
                for field, aliases in HEADERS.items():
                    hits = [i for i, cell in enumerate(row) if normalized(cell.value) in {normalized(a) for a in aliases}]
                    if len(hits) == 1:
                        found[field] = hits[0]
                if {'number', 'quantity'} <= found.keys():
                    mapping, header = found, index
                continue
            values = {key: '' if row[col].value is None else str(row[col].value) for key,col in mapping.items()}
            if not any(values.values()) or any(normalized(cell.value) in ('합계', '총계', '소계') for cell in row):
                continue
            if not any(values.get(key) for key in ('number','quantity','product','work_date','cost','external_id','line')):
                continue  # Secondary facility-address/contact header, not a business row.
            errors = []
            for key,col in mapping.items():
                if row[col].data_type == 'f':
                    errors.append(sorting.problem('SORT_E09', key, '수식 실행 금지: 원본 계산값·의미 확인 필요'))
            try:
                sorting.kg(values['quantity'])
            except sorting.SortingError as exc:
                errors.append(sorting.problem(exc.code, exc.field, exc.message))
            if not values['number']:
                errors.append(sorting.problem('SORT_E02', 'number', '신고번호 미확인'))
            result.append(dict(sheet=sheet_name, row=index, cells={k:f'{get_column_letter(c+1)}{index}' for k,c in mapping.items()}, original=values, issues=errors, matching='UNVERIFIED', profile_version='headers-1'))
        if header is None:
            raise ValueError('신고번호와 순중량 머리글을 찾을 수 없습니다.')
        return dict(sheets=sheets, rows=result, mapping={k:get_column_letter(c+1) for k,c in mapping.items()}, header=header, requires_sheet_selection=False, missing=[k for k in HEADERS if k not in mapping])
    finally:
        book.close()


def workbook_bytes(book):
    for sheet in book:
        sheet.freeze_panes = 'A2'
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 28
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def template():
    book = Workbook()
    book.active.title = '선별 검토'
    book.active.append(['신고번호', '품목란', '작업 ID', '시설 ID', '작업일', '품목', '배분 순중량(kg)'])
    book.active.append(['SYNTHETIC-26-000001X', '001', 'SYNTHETIC-BATCH-1', '가상시설', '2026-03-01', '가상품목', '100.000001'])
    return workbook_bytes(book)


def report(review):
    book = Workbook()
    sheet = book.active
    sheet.title = '검토용 지급 미확정'
    sheet.append(['검토용 / 지급 미확정', '공식 신청서 및 사무국 발급 확인서 아님'])
    sheet.append(['신청 ID', str(review['id']), '상태', review['state']])
    sheet.append(['배분 ID', '신고 원장 ID', '작업 ID', '신청 물량 kg', '원본 revision','행 상태','예약 유지','현재 원본 kg','원본 차이 kg','최근 사유'])
    for line in review['lines']:
        sheet.append([str(line['id']), str(line['declaration_id']), str(line['batch_id']), line['quantity'], str(line['source']['revision']),
                      line['state'],'유지' if line['active'] else '해제',line.get('current_kg'),line.get('quantity_delta'),line.get('reason')])
    sheet.append(['원래 신청량 kg',review.get('original_quantity',review['quantity']),'현재 예약량 kg',review['quantity']])
    errors = book.create_sheet('보완 오류')
    errors.append(['코드', '필드', '내용','배분 ID'])
    for issue in review['issues']:
        errors.append([issue['code'], issue['field'], issue['message'],str(issue.get('allocation_id',''))])
    dates=book.create_sheet('날짜 원본 대조')
    dates.append(['검토용 / 지급 미확정','원장 기재 선적일은 실제 수출이행 확인이 아님'])
    dates.append(['배분 ID','선별일','출고일','신고일','원장 기재 선적일','실제 수출이행일 (로컬 검토기록)'])
    for line in review['lines']:
        values=line.get('dates',{})
        dates.append([str(line['id'])]+[values.get(k) for k in ('work_date','dispatch_date','declared','planned_shipped','actual_shipped')])
    linked=book.create_sheet('행별 증빙 검토기록')
    linked.append(['로컬 검토기록 / 운영 승인 아님','독립 검토자 및 발급자 인증 미확인'])
    linked.append(['배분 ID','연결 상태','이행 검토 상태','확인서 대사 상태','기록 ID','기재 이행일','확인서 ID','이행증빙 ID','검토 표기자','기록 시각','검토 사유'])
    for line in review['lines']:
        assessment=line.get('evidence_review',{})
        linked.append([str(line['id'])]+[str(assessment.get(k) or '') for k in ('status','fulfillment_status','certificate_status','record_id','recorded_actual_shipped','certificate_id','fulfillment_evidence_id','actor','time','reason')])
    application=book.create_sheet('9.1 신청서 검토 초안')
    application.append(['검토용 / 지급 미확정','2026.9.1 별지11호 항목 참조; 공식 제출용 아님'])
    application.append(['항목','사업코드','물량 kg','상태'])
    for label,code in [('전체 공동선별 취급물량','A'),('생산유통통합조직 수혜물량','B'),('수출통합조직 수혜물량','C'),('전문단지 수혜물량','D'),('기타 유사사업 수혜물량','E'),('신청물량 A−B−C−D−E','F')]:
        application.append([label,code,None,'외부원장·동일 물량 부분 대사 미완료'])
    application.append(['이번 검토 예약량 (F 확정값 아님)','',review['quantity'],'검토용'])
    application.append(['G 한도단가 원/kg',None,'2027 미확정'])
    application.append(['F×G 신청한도',None,'미확정'])
    application.append(['분리 적격비용 검토 후보 원',review.get('reference_cost'), '원본·정상 이체 대사 후보; 승인 아님'])
    application.append(['승인액',None,'미확정'])
    application.append(['지급가능액',None,'미확정'])
    certificate=book.create_sheet('확인서 작성 참고 초안')
    certificate.append(['검토용 / 발급 확인서 아님','실제 사무국의 발급·심사·서명이 별도로 필요'])
    certificate.append(['검토 신청 ID',str(review['id'])])
    certificate.append(['검토 예약량 kg',review['quantity']])
    certificate.append(['발급주체',None,'미확인'])
    certificate.append(['품목',review['data'].get('product')])
    certificate.append(['회원·시설·기간',None,'원본 증빙으로 확인 필요'])
    certificate.append(['중복여부',None,'외부원장 미조회'])
    audit=book.create_sheet('검토 근거')
    audit.append(['검토용 / 지급 미확정', '원본 hash는 전자서명이 아님'])
    audit.append(['프로파일',review['data']['profile']])
    audit.append(['생성시각',review['data']['created']])
    audit.append(['증빙·신고 원본 ID','revision','데이터 hash'])
    for line in review['lines']:
        audit.append([str(line['source']['id']),str(line['source']['revision']),line['source']['hash']])
    return workbook_bytes(book)
