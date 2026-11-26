"""Printable review forms: escaped HTML, no official signatures or payout fields."""
from html import escape


def text(value):
    return escape('미확인' if value is None or value=='' else str(value),quote=True)


def render(review):
    data=review['data']
    product=text(data.get('product'))
    quantity=text(review['quantity'])
    work_dates=[line.get('dates',{}).get('work_date') for line in review['lines'] if line['active'] and line.get('dates',{}).get('work_date')]
    period='미확인' if not work_dates else text(min(work_dates)+' ~ '+max(work_dates))
    fields=[('A','전체 공동선별 취급물량'),('B','생산유통통합조직 공동선별비 수혜물량'),('C','수출통합조직 공동선별비 수혜물량'),('D','전문단지 지원 수혜물량'),('E','기타 유사 사업 지원 수혜물량'),('F','신청물량 A − B − C − D − E')]
    rows=''.join(f'<tr><th scope="row">{code}</th><td>{label}</td><td>미확인</td><td>kg</td></tr>' for code,label in fields)
    lines=''.join(f'<tr><td>{text(line["id"])}</td><td>{text(line["source"]["original"].get("number"))}</td><td>{text(line["source"]["original"].get("line"))}</td><td>{text(line["quantity"])}</td><td>{"예약 유지" if line["active"] else "해제 이력"}</td></tr>' for line in review['lines'])
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>공동선별비 검토용 신청서</title><style>
*{{box-sizing:border-box}}body{{font:13px/1.55 "Malgun Gothic",sans-serif;color:#111;background:#eee;margin:0}}main{{max-width:210mm;margin:16px auto;background:white;padding:16mm}}h1{{font-size:21px;text-align:center;margin:0 0 8px}}h2{{font-size:15px;margin:20px 0 7px}}p{{margin:7px 0}}.stamp{{font-weight:bold;text-align:center;border:2px solid #333;padding:8px}}table{{width:100%;border-collapse:collapse;margin:8px 0}}th,td{{border:1px solid #777;padding:8px;overflow-wrap:anywhere}}th{{background:#f2f2f2;text-align:left}}.number{{text-align:right}}.note{{font-size:12px}}.certificate{{break-before:page;page-break-before:always}}.detail{{break-before:page;page-break-before:always}}tr{{break-inside:avoid}}.toolbar{{max-width:210mm;margin:12px auto}}button{{font:inherit;padding:8px 16px}}@page{{size:A4 portrait;margin:14mm}}@media print{{body{{background:white}}main{{margin:0;padding:0;max-width:none}}.toolbar{{display:none}}thead{{display:table-header-group}}}}
@media print{{body{{font-size:12px;line-height:1.4}}th,td{{padding:5px}}h1{{font-size:20px}}h2{{margin:12px 0 5px}}table{{margin:5px 0}}p{{margin:5px 0}}}}
</style><div class="toolbar"><button onclick="window.print()">검토 서식 인쇄</button><p>브라우저 인쇄에서 PDF 저장을 선택할 수 있습니다.</p></div><main>
<section><h1>{text(data.get('year'))} 공동선별비 검토용 정산신청서</h1><p class="stamp">검토용 / 지급 미확정 / 공식 제출용 아님</p>
<p class="note">2026년 9월 1일 별지 제11호의 항목을 참조한 검토 초안입니다. 2027 정책·서식 확정을 의미하지 않습니다.</p>
<h2>1 신청업체</h2><table><tr><th>신청업체명</th><td>미확인</td><th>담당자</th><td>미확인</td></tr><tr><th>생산자 또는 단체</th><td>미확인</td><th>연락처</th><td>미확인</td></tr></table>
<h2>2 신청내용</h2><table><thead><tr><th>구분</th><th>항목</th><th>기재량</th><th>단위</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">B~E는 동일 물량 부분의 합집합으로 대사해야 하며 공란을 0으로 간주하지 않습니다. 외부 중복원장은 미조회입니다.</p>
<h2>3 품목별 신청액 산정내역</h2><table><thead><tr><th>품목</th><th>F 신청물량 kg</th><th>G 한도단가 원/kg</th><th>F × G 신청한도 원</th></tr></thead><tbody><tr><td>{product}</td><td>미확인</td><td>미확정</td><td>미확정</td></tr></tbody></table>
<table><tr><th>현재 검토 예약량</th><td>{quantity} kg (F 확정값 아님)</td></tr><tr><th>분리 적격비용 검토 후보</th><td>{text(review.get('reference_cost'))} 원</td></tr><tr><th>실비 참조 검토액</th><td>미확정 · 정책 단가 및 비용 심사 필요</td></tr><tr><th>승인액 및 지급가능액</th><td>미확정</td></tr></table>
<p class="note">신청한도·실비 검토액·승인액·지급액은 별개입니다. 단가와 공통 보조율을 임의 적용하지 않습니다. 필수 첨부 총수 6종의 전체 목록은 미확보로 추정하여 작성하지 않습니다.</p>
<p>검토 메모: __________________________________________________________________</p></section>
<section class="certificate"><h1>공동정산 확인서 작성 참고 초안</h1><p class="stamp">검토용 / 사무국 발급 확인서 아님</p>
<table><tr><th>실제 발급 사무국</th><td>미확인</td></tr><tr><th>품목</th><td>{product}</td></tr><tr><th>회원 및 지정 시설</th><td>발급 원본으로 별도 확인</td></tr><tr><th>신청행 선별기간 참고</th><td>{period}</td></tr><tr><th>현재 예약량 참고</th><td>{quantity} kg</td></tr><tr><th>사무국 확인물량</th><td>미확인</td></tr><tr><th>타 사업 중복 여부</th><td>외부원장 미조회</td></tr><tr><th>발급 및 서명 확인</th><td>공식 사무국 절차에서 별도 확인</td></tr></table>
<p>프로그램이 생성한 이 초안은 발급·서명·지원 적격을 보증하지 않습니다. 연결된 확인서의 대사 상태는 행별 검토기록으로 확인합니다.</p></section>
<section class="detail"><h1>신고별 검토 세부내역</h1><p class="stamp">검토용 / 지급 미확정</p><p>검토 신청 ID {text(review['id'])} · 개정 {text(review['revision'])} · 프로파일 {text(data.get('profile'))}</p>
<table><thead><tr><th>배분 ID</th><th>신고번호 원문</th><th>품목란</th><th>검토량 kg</th><th>예약 상태</th></tr></thead><tbody>{lines}</tbody></table>
<p class="note">원래 신청량 {text(review.get('original_quantity'))} kg / 현재 예약량 {quantity} kg. 해제된 행도 과거 검토 이력으로 보존합니다. 원본·증빙 및 보완 사유는 검토 Excel에서 대조합니다.</p></section></main></html>'''
