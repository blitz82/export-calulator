"""Bound summary, original appendices, bookmarks and a verifiable originals archive."""
from io import BytesIO
from pathlib import Path
from hashlib import sha256
from decimal import Decimal
from html import escape
from zipfile import ZipFile,ZIP_DEFLATED
import json,os,re
from epms import store,sorting as s,settlement as st

def render(conn,org,result):
    from pypdf import PdfReader,PdfWriter
    from pypdf.annotations import Link
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
    from epms.settlement_pdf import LOCK
    with LOCK:
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'malgun.ttf'
        if not font.is_file():st.fail('한글 PDF 글꼴 Malgun Gothic 설치를 확인하세요.',503)
        if 'SettlementKorean' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SettlementKorean',str(font)))
        body=ParagraphStyle('monthly-body',fontName='SettlementKorean',fontSize=9,leading=13,wordWrap='CJK',spaceAfter=5)
        heading=ParagraphStyle('monthly-heading',parent=body,fontSize=13,leading=19,textColor=colors.HexColor('#204a36'),spaceBefore=10,keepWithNext=True)
        title=ParagraphStyle('monthly-title',parent=body,fontSize=21,leading=29,spaceAfter=18)
        width=A4[0]-76
        def p(v):return Paragraph(escape(str(v if v not in (None,'') else '미입력')).replace('\n','<br/>'),body)
        def money(v):return f'{int(Decimal(str(v))):,}원' if v is not None else '미입력'
        def table(rows,ratios):
            t=Table([[p(c) for c in row] for row in rows],colWidths=[width*r for r in ratios],repeatRows=1,hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7f0e9')),('GRID',(0,0),(-1,-1),.35,colors.HexColor('#b3c2b7')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));return t
        # Load once; original bytes are unchanged in the archive. Reject encrypted or invalid PDFs.
        files=[];seen={};raw_total=0;page_total=0
        for item in result['documents']:
            row=s.scoped(conn,s.evidence if item['kind']=='evidence' else store.import_files,org,item['file']);raw=row['content'] if item['kind']=='evidence' else row['data']
            digest=sha256(raw).hexdigest()
            if digest!=item['sha']:st.fail('증빙 해시가 변경되었습니다: '+item['name'],409)
            if digest in seen:
                seen[digest]['uses'].extend(u for u in item['uses'] if u not in seen[digest]['uses']);continue
            try:
                reader=PdfReader(BytesIO(raw),strict=False)
                if reader.is_encrypted:st.fail('암호화된 증빙 PDF를 해제한 사본으로 등록하세요: '+item['name'])
                count=len(reader.pages)
                if not count:st.fail('빈 PDF입니다: '+item['name'])
            except Exception as error:
                from fastapi import HTTPException
                if isinstance(error,HTTPException):raise
                st.fail('합칠 수 없는 증빙 PDF입니다: '+item['name'])
            for use in item['uses']:
                if not 1<=use['page']<=count:st.fail('증빙 근거 페이지가 범위를 벗어났습니다: '+item['name'])
            entry=dict(item,pages=count,raw=raw,sha=digest);seen[digest]=entry;files.append(entry);raw_total+=len(raw);page_total+=count
            if raw_total>150_000_000 or page_total>2500:st.fail('통합본은 원본 합계 150MB·2500쪽 이내로 나누어 작성하세요.',413)
        d=result['data'];t=result['totals']
        def summary(index,offset):
            story=[Paragraph('월별 공동선별비 정산·지급 보고',title),p(result['organization']),p(f"{d['year']}년 {d['month']}월 / {d['title']}"),p(f"대상 기간 {d['start']} ~ {d['end']}"),p('제출처: '+d['destination']),p('작성자: '+d['writer']),p('생성: '+result['created']),p('정정 대상: '+str(result['previous'] or '최초 보고')),p('본 문서는 사무국 입력 내용과 증빙을 묶은 제출용 자료이며 aT 승인·접수를 의미하지 않습니다.'),p('가정값·참고 계산 포함 여부: '+('포함' if result['warnings'] else '점검 경고 없음')),Paragraph('월별 총괄',heading),table([['구분','금액·물량'],['보고 배분 물량',t['kg']+' kg'],['지원 계산액',money(t['amount'])],['내부결정액',money(t['decision'])],['지출결의액',money(t['resolved'])],['지급 / 반환',money(t['paid'])+' / '+money(t['refund'])],['순지급액',money(t['net'])],['결의 대비 미지급액',money(t['unpaid'])]],[.42,.58]),Paragraph('보고 설명과 차이·예외',heading),p(d['note'] or '별도 설명 없음'),*[p(v) for v in result['warnings']],PageBreak(),Paragraph('회원사별 집계',heading),table([['회원사 / 사업자번호','계산 / 결정','결의 / 순지급','미지급 / 상태'],*[[m['name']+'\n'+m['number'],money(m['amount'])+'\n'+money(m['decision']),money(m['resolved'])+'\n'+money(m['net']),money(m['unpaid'])+'\n'+m['status']] for m in result['members']]],[.29,.24,.25,.22])]
            for m in result['members']:
                review=m.get('review',{});story.extend([Paragraph(m['name']+' - 검토·집행 상세',heading),p('내부검토: '+review.get('date','')+' / '+review.get('reviewer','')),p(review.get('note') or '검토 메모 없음'),p('계산·결정·결의 차이 사유: '+(review.get('difference_reason') or '차이 없음')),p('미지급 사유 / 예정일: '+(review.get('unpaid_reason') or '해당 없음')+' / '+(review.get('expected_date') or '미정'))])
                events=[['구분','문서·거래 / 일자','이번 회원사 배분액']]
                for e in d['resolutions']:
                    for a in e['allocations']:
                        if a['member_id']==m['member_id']:events.append(['지출결의',e['number']+' / '+e['date']+'\n문서 총액 '+money(e['total'])+' / 사업 대상 '+money(e['scope_amount']),money(a['amount'])])
                for e in d['payments']:
                    for a in e['allocations']:
                        if a['member_id']==m['member_id']:events.append(['반환' if e['refund'] else '지급',e['reference']+' / '+e['date']+'\n수취인 '+e['recipient']+'\n'+e['recipient_reason'],money(a['amount'])])
                if len(events)>1:story.append(table(events,[.16,.64,.2]))
            for selected in result['selections']:
                snap=selected['snapshot'];r=snap['result'];pol=r.get('policy',snap['data']['policy']);support=r.get('support')
                story.extend([PageBreak(),Paragraph('계산 근거 - '+snap['data']['title'],heading),p('공동선별장: '+snap['facility']['name']),p('계산 확정일: '+snap['time']+' / '+str(snap.get('confirmation_number',snap['revision']))+'차'),p('지원기준: '+pol['label']+' / '+(support['status']+' / '+str(support['revision'])+'차' if support else '작업별 참고 계산')),p('근거: '+(pol['reference'] or '가정값')),p('산식: '+r['formula']),p('단가 '+pol['unit_price']+'원/kg / '+('보조율 포함' if pol['rate_included'] else '보조율 '+pol['rate']+'%')+' / '+pol['rounding']),p('허용 차이: '+(pol['tolerance_kg'] or '미설정')+'kg / '+(pol['tolerance_percent'] or '미설정')+'% / '+pol['tolerance_rule']),p('이번 보고 배분 사유: '+(selected['reason'] or '확정 결과 전액·전체 물량')),table([['회원사','이번 보고액 / 물량','원 확정 귀속액 / 물량'],*[[a['name'],money(a['amount'])+' / '+a['kg']+'kg',money(a['base_amount'])+' / '+a['base_kg']+'kg'] for a in selected['portions']]],[.34,.33,.33])])
                if support:story.append(table([['회원사','배정 잔액 / 계산액','지원 반영 / 미지원'],*[[a['name'],money(a['remaining'])+' / '+money(a['base_amount']),money(a['amount'])+' / '+money(a['unfunded'])] for a in support['members']]],[.34,.33,.33]))
                rows=[['선별기록 / 생산자','신고번호 / 품목란 / HS','연결 kg 선별 / 신고','차이 설명']]
                for e in r['lines']:
                    dec=e['declaration_data'];rows.append([e['batch_title']+'\n'+e['producer']['name'],dec['number']+' / '+dec['line']+'\nHS '+dec.get('hs','')+'\n수출화주 '+dec.get('owner_name','')+'\n대행 '+dec.get('agent_name',''),e['left']+' / '+e['right'],e['reason']+'\n'+e['exception_reason']+'\n'+e['period_reason']])
                story.extend([p('아래 연결 명세는 원 확정 전체 내역입니다. 일부 보고 시 이번 보고 배분은 위 표를 따릅니다.'),table(rows,[.27,.34,.17,.22])])
            story.extend([PageBreak(),Paragraph('증빙 색인',heading),p('원본 PDF는 문서별 한 번만 첨부합니다. 통합본은 열람용 사본이며 원본 서명·원본 바이트는 증빙 ZIP에서 보존합니다.')])
            rows=[['번호 / 문서명','용도·원본 근거 쪽','통합본 쪽']]
            for i,f in enumerate(files):
                start=index[i] if index else 0
                for n,u in enumerate(f['uses']):
                    use=f"{u['label']} / 원본 {u['page']}쪽 → 통합 {start+u['page']-1 if start else '준비'}쪽"
                    rows.append([str(i+1)+'. '+f['name'] if n==0 else '같은 문서',use,f"{start} ~ {start+f['pages']-1}" if start else '준비 중'])
            story.append(table(rows,[.32,.48,.2]));out=BytesIO()
            def footer(canvas,doc):
                canvas.setFont('SettlementKorean',8);canvas.drawRightString(A4[0]-38,23,f'{doc.page}쪽 / 제출자료')
            SimpleDocTemplate(out,pagesize=A4,leftMargin=38,rightMargin=38,topMargin=34,bottomMargin=38,title=d['title'],author=d['writer']).build(story,onFirstPage=footer,onLaterPages=footer)
            return out.getvalue()
        toc_count=(len(files)+19)//20
        index=None;raw=summary(None,0)
        # Index text affects pagination: converge before freezing, never emit stale page references.
        for _ in range(8):
            count=len(PdfReader(BytesIO(raw)).pages);new=[];start=count+toc_count+1
            for f in files:new.append(start);start+=f['pages']
            updated=summary(new,count)
            if new==index and len(PdfReader(BytesIO(updated)).pages)==count:raw=updated;break
            index=new;raw=updated
        else:st.fail('증빙 색인 페이지를 배치하지 못했습니다. 보고 범위를 나누어 다시 생성하세요.')
        writer=PdfWriter();writer.append(BytesIO(raw),import_outline=False);writer.add_outline_item('보고 총괄·회원사별 내역',0)
        from reportlab.pdfgen.canvas import Canvas
        toc=BytesIO();canvas=Canvas(toc,pagesize=A4);summary_count=len(writer.pages)
        for page_index in range(toc_count):
            canvas.setFont('SettlementKorean',15);canvas.drawString(38,A4[1]-50,'증빙 원문 바로가기')
            canvas.setFont('SettlementKorean',9)
            for n,f in enumerate(files[page_index*20:(page_index+1)*20]):
                i=page_index*20+n;y=A4[1]-90-n*32
                label=f"{i+1}. {f['name']}"
                while pdfmetrics.stringWidth(label,'SettlementKorean',9)>width-65:label=label[:-2]+'…'
                canvas.drawString(38,y,label);canvas.drawRightString(A4[0]-38,y,str(index[i])+'쪽')
            canvas.showPage()
        canvas.save()
        if toc_count:writer.append(BytesIO(toc.getvalue()),import_outline=False)
        manifest=[]
        for i,f in enumerate(files):
            start=len(writer.pages);writer.append(BytesIO(f['raw']),import_outline=False);writer.add_outline_item(f['name'],start)
            manifest.append({k:v for k,v in dict(f,start=start+1,end=len(writer.pages)).items() if k!='raw'})
            y=A4[1]-90-(i%20)*32
            writer.add_annotation(summary_count+i//20,Link(rect=(38,y-5,A4[0]-38,y+15),target_page_index=start))
        # Clickable index/bookmarks plus unobtrusive page labels outside the original page.
        for i,page in enumerate(writer.pages):
            if i<summary_count+toc_count:continue
            page.transfer_rotation_to_content()
            w=float(page.mediabox.width);h=float(page.mediabox.height);overlay=BytesIO();c=Canvas(overlay,pagesize=(w,h+18));c.setFont('SettlementKorean',7);c.drawRightString(w-8,h+5,f'통합 {i+1}쪽 / 원본 사본');c.save()
            from pypdf.generic import RectangleObject
            box=page.cropbox
            page.mediabox=RectangleObject([page.mediabox.left,page.mediabox.bottom,page.mediabox.right,h+18]);page.cropbox=RectangleObject([box.left,box.bottom,box.right,box.top+18]);page.merge_page(PdfReader(overlay).pages[0])
        writer.add_metadata({'/Title':d['title'],'/Author':d['writer']});pdf=BytesIO();writer.write(pdf)
        archive=BytesIO()
        with ZipFile(archive,'w',ZIP_DEFLATED) as z:
            z.writestr('통합 제출자료.pdf',pdf.getvalue())
            for i,f in enumerate(files):z.writestr(f"원본/{i+1:03d}_"+re.sub(r'[\\/:*?"<>|]','_',f['name']),f['raw'])
            from epms.workbench import imports
            for key in sorted({p['import_source'] for p in d['payments'] if p.get('import_source')}):
                original=s.scoped(conn,imports,org,key)
                z.writestr('지급명세/'+str(key)+'_'+re.sub(r'[\\/:*?"<>|]','_',original['name']),original['content'])
            frozen=deepcopy_result(result);frozen['manifest']=manifest
            z.writestr('제출자료_목록.json',json.dumps(frozen,ensure_ascii=False,indent=2))
            z.writestr('통합본_SHA256.txt',sha256(pdf.getvalue()).hexdigest())
        return pdf.getvalue(),archive.getvalue(),manifest

def deepcopy_result(result):
    return {k:v for k,v in result.items() if k!='transfer_claims'}
