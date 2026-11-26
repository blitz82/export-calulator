"""Conservative compound-page grouping layered over the existing OCR engine."""
import re
from io import BytesIO
import pypdfium2 as pdfium
from epms import ocr, sorting as s


def group_pages(pages):
    groups={}
    documents=[]
    for page in pages:
        text=page.get('text','')
        rows=page.get('rows',[])
        if rows and re.search(r'수\s*출\s*신\s*고\s*필\s*증',text):
            kind='DECLARATION'
        elif re.search(r'AIR\s*WAYBILL|AIR\s*WAY\s*BILL|BILL\s*OF\s*LADING|AWB|항공화물운송장',text,re.I):
            kind='TRANSPORT'
        elif rows or '수출신고필증' in text:
            kind='DECLARATION'
        else:
            kind='OTHER'
        documents.append(dict(page=page['page'],kind=kind,status='REVIEW_REQUIRED',warning=page.get('warning','')))
        if kind!='DECLARATION':
            continue  # Transport gross weight never becomes declaration net weight.
        for index,row in enumerate(rows):
            number=s.normalize_number(row.get('number'))
            line=str(row.get('line',''))
            if not number or not line:
                documents[-1]['warning']='신고번호·품목란 누락: 연결 보류'
                continue
            key=number+':'+line+':'+str(row.get('version',''))
            group=groups.setdefault(key,dict(key=key,number_original=row.get('number'),line=line,version=row.get('version'),
                pages=[],candidates=[],net_weight=None,conflict=False,status='REVIEW_REQUIRED',transport_candidates=[]))
            group['pages'].append(page['page'])
            source=page.get('evidence',[])
            group['candidates'].append(dict(page=page['page'],values=row,evidence=source[index] if index<len(source) else {},confidence='UNVERIFIED'))
            weight=row.get('kg')
            if weight is not None:
                old=s.rules.decimal(group['net_weight']) if group['net_weight'] is not None else None
                new=s.rules.decimal(weight)
                if group['net_weight'] is not None and (old is None or new is None or old!=new):
                    group['conflict']=True
                elif group['net_weight'] is None:
                    group['net_weight']=weight
    # Candidates only: absence of a shared identifier never creates a confirmed link.
    for group in groups.values():
        group['transport_candidates']=[p['page'] for p in documents if p['kind']=='TRANSPORT']
        group['pages']=sorted(set(group['pages']))
    return dict(documents=documents,groups=list(groups.values()),automatic_approval=False,
                total_net_weight=None,label='복합 문서 후보 / 원본 대조 필요')


def extract(data,load_chunk=None,save_chunk=None):
    pages=[]
    with ocr.PDF_LOCK,ocr.open_pdf(data) as document:
        if not 1<=len(document)<=150:
            raise ValueError('복합 PDF는 1~150쪽이어야 합니다.')
        for start in range(0,len(document),ocr.MAX_PAGES):
            cached=load_chunk(start) if load_chunk else None
            if cached is not None:
                pages.extend(cached)
                continue
            with pdfium.PdfDocument.new() as chunk:
                chunk.import_pages(document,pages=list(range(start,min(start+ocr.MAX_PAGES,len(document)))))
                output=BytesIO()
                chunk.save(output)
            extracted=ocr.extract(output.getvalue())
            for page in extracted:
                page['page']+=start
                for evidence in page['evidence']:
                    for field in evidence.values():
                        if isinstance(field,dict) and 'page' in field:
                            field['page']+=start
            if save_chunk:
                save_chunk(start,extracted)
            pages.extend(extracted)
    return group_pages(ocr.stitch_continuations(pages))
