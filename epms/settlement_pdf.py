"""A4 Korean settlement summaries, generated from immutable snapshots."""
from io import BytesIO
from pathlib import Path
import os
from html import escape
from threading import RLock

LOCK=RLock()

def render(snapshot):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
    with LOCK:
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'malgun.ttf'
        if not font.is_file():
            from fastapi import HTTPException
            raise HTTPException(503,'한글 PDF 글꼴 Malgun Gothic을 찾을 수 없습니다. Windows 한글 글꼴 설치를 확인하세요.')
        if 'SettlementKorean' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SettlementKorean',str(font)))
        style=ParagraphStyle('body',fontName='SettlementKorean',fontSize=9,leading=13,wordWrap='CJK',spaceAfter=3)
        title=ParagraphStyle('title',parent=style,fontSize=17,leading=23,spaceAfter=12)
        section=ParagraphStyle('section',parent=style,fontSize=11,leading=17,spaceBefore=8,spaceAfter=5,textColor=colors.HexColor('#204a36'))
        def p(value):return Paragraph(escape(str(value if value not in (None,'') else '미입력')).replace('\n','<br/>'),style)
        def table(rows,widths):
            t=Table([[p(v) for v in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7f0e9')),('VALIGN',(0,0),(-1,-1),'TOP'),
                ('GRID',(0,0),(-1,-1),.35,colors.HexColor('#b3c2b7')),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            return t
        d=snapshot['data'];r=snapshot['result'];policy=r.get('policy',d['policy']);out=BytesIO();width=A4[0]-76
        story=[Paragraph('공동선별비 정산 결과',title),p(r['label']),p(d['title']),
            p(f"{snapshot['organization']} / {snapshot['facility']['name']} / {d['year']}년 / {d['start']} ~ {d['end']}"),
            p(f"작성 {snapshot['time']} / 확정 {snapshot.get('confirmation_number',snapshot['revision'])}차"),
            Paragraph('물량과 계산 요약',section),table([['항목','결과'],['선별 측 연결량',r['left']+' kg'],['신고 측 연결 순중량',r['right']+' kg'],
                ['양쪽 차이',r['difference']+' kg'],['계산 적용 물량',r['billable']+' kg'],['배부 지급 비용',r['allocated_cost']+' 원'],['지원 예상액',r['amount']+' 원']],[width*.4,width*.6]),
            Spacer(1,8),p('산식: '+r['formula']),p('물량 기준: '+r['quantity_basis']),p('시설·연도 공동 한도: '+(policy['scope_cap'] or '미설정')+' 원'),
            p('시설·연도 기존 사용액: '+r.get('scope_used','0')+' 원 / 이번 계산 전 한도 잔액: '+(r.get('scope_available') or '한도 미설정')),
            p('계산 기준: '+policy['label']+' / '+r.get('calculation_version','settlement-reference-1')),p('근거·가정: '+(policy['reference'] or '사용자 가정값')),
            p('중량 차이 허용: '+(policy['tolerance_kg'] or '미설정')+' kg / '+(policy['tolerance_percent'] or '미설정')+'% / '+policy['tolerance_rule']),
            p(r['tolerance_basis']),Paragraph('연결 내역과 잔량',section)]
        rows=[['선별기록·생산자','신고번호 / 품목란','연결 kg (선별 / 신고)','잔량 kg (선별 / 신고)']]
        for e in r['lines']:
            rows.append([e['batch_title']+'\n'+e['producer']['name'],e['declaration_data']['number']+' / '+e['declaration_data']['line'],e['left']+' / '+e['right'],e['left_remaining']+' / '+e['right_remaining']])
        story.append(table(rows,[width*.28,width*.3,width*.21,width*.21]))
        if r.get('support'):
            budget=r['support'];story.extend([Paragraph('사업연도 지원기준·회원사 배정 반영',section),p(f"{budget['title']} / {budget['status']} / 기준 {budget['revision']}차 / 배정 대상: 생산자회원사"),p('근거: '+budget['reference']),p(f"전체 예산 {budget['total']}원 / 이번 계산 전 사용액 {budget['used']}원 / 잔액 {budget['remaining']}원")])
            if budget.get('effective_period'):
                term=budget['effective_period'];story.append(p('단가 적용: '+term['start']+' ~ '+term['end']+' / 날짜 기준 '+budget.get('date_basis','선별기간')))
            story.append(table([['회원사 / 매칭 kg','계산액 / 배정 잔액','이번 반영 / 미반영'],*[[a['name']+'\n'+a['matched_kg']+' kg',a['base_amount']+' / '+a['remaining']+' 원',a['amount']+' / '+a['unfunded']+' 원'] for a in budget['members']]],[width*.36,width*.32,width*.32]))
            story.append(p('한도 미반영 금액은 물량 잔량이 아닙니다. 증액 후 같은 정산을 재검토하며 과거 PDF는 유지됩니다.'))
            story.append(p('직전 활성 확정액: '+budget.get('previous_amount','0')+'원 / 이번 재확정 증감: '+budget.get('delta',budget['amount'])+'원 (지급 완료액 아님)'))
        if r['costs']:
            story.append(Paragraph('비용 배부 근거',section))
            story.append(table([['비용 사건','배부 기준','적용액'],*[[c['title'],c['method']+' / '+c['reason'],c['amount']+' 원'] for c in r['costs']]],[width*.32,width*.48,width*.2]))
        notes=r['warnings']+[e['reason']+' '+e['exception_reason']+' '+e['period_reason'] for e in r['lines'] if e['reason'] or e['exception_reason'] or e['period_reason']]
        if notes:story.extend([Paragraph('차이·기간 확인 사항',section),*[p(n) for n in dict.fromkeys(notes)]])
        evidence={}
        for e in r['lines']:
            for ref in e['evidence_refs']+e['source_evidence']:evidence[(ref['sha'],ref['page'])]=ref
        for c in r['costs']:
            for ref in c['evidence_refs']:evidence[(ref['sha'],ref['page'])]=ref
        story.extend([Paragraph('증빙 원본과 근거 페이지',section),*[p(f"{ref['name']} / {ref['page']}쪽") for ref in evidence.values()],Spacer(1,8),p('이 문서는 입력한 가정과 확인 내용을 보존한 참고 정산 결과이며 외부 기관 승인 또는 지급 완료를 의미하지 않습니다.')])
        def footer(canvas,doc):
            canvas.setFont('SettlementKorean',8);canvas.drawRightString(A4[0]-38,23,f'{doc.page}쪽')
        SimpleDocTemplate(out,pagesize=A4,rightMargin=38,leftMargin=38,topMargin=34,bottomMargin=38,title='공동선별비 정산 결과',author='통합조직 사무국').build(story,onFirstPage=footer,onLaterPages=footer)
        return out.getvalue()
