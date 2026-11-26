"""Page checkpoints and locally reviewed OCR candidates, never an export authority."""
from io import BytesIO
import hashlib
import pypdfium2 as pdfium
from epms import ocr,store,sorting as s,sorting_documents as documents,sorting_books

VERSION='pdf-review-1'
CACHE='PDF_PAGE_CANDIDATES'
REVIEW='PDF_PAGE_REVIEW'


def source(conn,org,evidence_id):
    row=s.scoped(conn,s.evidence,org,evidence_id)
    if hashlib.sha256(row['content']).hexdigest()!=row['sha']:
        s.reject('SORT_E08','evidence','PDF 원본 해시가 변경되었습니다.')
    return row


def extract_page(content,page):
    with ocr.PDF_LOCK,ocr.open_pdf(content) as original:
        if not 1<=len(original)<=150 or not 1<=page<=len(original):
            raise ValueError('PDF는 최대 150쪽이며 유효한 쪽 번호가 필요합니다.')
        with pdfium.PdfDocument.new() as part:
            part.import_pages(original,pages=[page-1])
            output=BytesIO();part.save(output)
    result=ocr.extract(output.getvalue())[0]
    for row in result['evidence']:
        for value in row.values():
            if isinstance(value,dict) and 'page' in value:
                value['page']=page
    result['page']=page
    return result


def cached(conn,org,sha):
    rows=conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==CACHE).order_by(s.records.c.id)).mappings()
    result={}
    for row in rows:
        value=row['data']
        if value['sha']==sha and value['version']==VERSION and value['extractor']==ocr.EXTRACTION_VERSION and s.digest(value['result'])==value['result_hash']:
            result[value['page']]=dict(row)
    return result


def status(conn,org,evidence_id):
    file=source(conn,org,evidence_id)
    with ocr.PDF_LOCK,ocr.open_pdf(file['content']) as pdf:
        count=len(pdf)
    if not 1<=count<=150:
        raise ValueError('PDF는 1~150쪽이어야 합니다.')
    pages=cached(conn,org,file['sha'])
    reviews={}
    for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind==REVIEW).order_by(s.records.c.id)).mappings():
        if r['data']['sha']==file['sha']:
            reviews[r['data']['page']]=r['data']
    states=[]
    for page in range(1,count+1):
        cache=pages.get(page)
        review=reviews.get(page)
        state='UNPROCESSED' if cache is None else 'EXTRACTED_UNREVIEWED'
        if cache and review:
            state=review['state'] if review['extraction_hash']==cache['data']['result_hash'] else 'STALE_REVIEW'
        states.append(dict(page=page,state=state,rows=0 if cache is None else len(cache['data']['result']['rows']),
                           warning='' if cache is None else cache['data']['result'].get('warning',''),
                           extraction_hash=None if cache is None else cache['data']['result_hash']))
    grouped=documents.group_pages([r['data']['result'] for _,r in sorted(pages.items())])
    return dict(evidence_id=evidence_id,sha=file['sha'],pages=count,processed=len(pages),page_states=states,
                groups=grouped['groups'],automatic_approval=False,identity_assurance='LOCAL_UNVERIFIED',
                label='OCR 후보 / 원본 대조 및 별도 신고 검증 필요')


def process(db,org,evidence_id,start,count,force=False):
    with db.connect() as conn:
        file=source(conn,org,evidence_id)
        known=cached(conn,org,file['sha'])
    with ocr.PDF_LOCK,ocr.open_pdf(file['content']) as pdf:
        total=len(pdf)
    if not 1<=total<=150 or not 1<=count<=5 or start<1 or start+count-1>total:
        raise ValueError('처리 쪽 범위를 확인하세요. 최대 150쪽입니다.')
    for page in range(start,start+count):
        if not force and page in known:
            continue
        result=extract_page(file['content'],page)
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            current=source(conn,org,evidence_id)
            if current['sha']!=file['sha']:
                s.reject('SORT_E08','evidence','처리 중 PDF 원본이 변경되었습니다.')
            latest=cached(conn,org,file['sha']).get(page)
            if latest and not force:
                continue
            conn.execute(s.records.insert().values(org=org,kind=CACHE,revision=1,data=dict(sha=file['sha'],page=page,
                version=VERSION,extractor=ocr.EXTRACTION_VERSION,result=result,result_hash=s.digest(result),time=store.now())))
    with db.connect() as conn:
        return status(conn,org,evidence_id)


def review_page(conn,org,evidence_id,page,extraction_hash,state,reason,actor):
    file=source(conn,org,evidence_id)
    row=cached(conn,org,file['sha']).get(page)
    if not row or row['data']['result_hash']!=extraction_hash:
        s.reject('SORT_E08','extraction_hash','추출 결과가 없거나 변경되었습니다. 다시 조회하세요.')
    if state not in {'LOCALLY_REVIEWED','NEEDS_SUPPLEMENT'}:
        s.reject('SORT_E08','state','쪽 검토 상태를 확인하세요.',422)
    conn.execute(s.records.insert().values(org=org,kind=REVIEW,revision=1,data=dict(sha=file['sha'],page=page,
        extraction_hash=extraction_hash,state=state,reason=reason,actor=actor,time=store.now(),identity_assurance='LOCAL_UNVERIFIED')))
    store.log(conn,org,actor,'sorting_pdf_page_review',evidence_id,reason,None,dict(page=page,state=state))
    return dict(state=state,automatic_approval=False)


def compare_excel(conn,org,pdf_id,excel_id,sheet):
    info=status(conn,org,pdf_id)
    excel=source(conn,org,excel_id)
    parsed=sorting_books.preview(excel['content'],sheet)
    result=[]
    for row in parsed['rows']:
        original=row['original'];number=s.normalize_number(original['number'])
        matches=[]
        for group in info['groups']:
            full=s.normalize_number(group['number_original'])
            if number and (full==number or (full[:-1]==number and full[-1:].isalpha())):
                matches.append(group)
        weight=None
        if len(matches)==1 and not matches[0]['conflict']:
            weight=s.rules.decimal(matches[0]['net_weight'])
        quantity=s.rules.decimal(original['quantity'])
        difference=None if weight is None or quantity is None else str(weight-quantity)
        result.append(dict(sheet=row['sheet'],row=row['row'],number_original=original['number'],excel_quantity=original['quantity'],
            status='NO_CANDIDATE' if not matches else 'AMBIGUOUS' if len(matches)>1 else 'CANDIDATE_ONLY',
            pdf_candidates=matches,difference=difference,weight_status='UNKNOWN' if difference is None else 'MATCH' if s.rules.decimal(difference)==0 else 'DIFFERENCE',
            item_line_status='UNVERIFIED',physical_export_verified=False))
    return dict(pdf=info,excel_id=excel_id,excel_sha=excel['sha'],rows=result,automatic_approval=False,
                label='PDF OCR 후보와 표 비교 / 자동 신고 매칭 아님',payable_amount=None)
