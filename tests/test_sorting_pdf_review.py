from io import BytesIO
import pytest
from reportlab.pdfgen import canvas
from epms import sorting_pdf_review as review,sorting_documents as documents,sorting as s
from test_sorting import setup
from test_sorting_reconciliation import source


def pdf():
    output=BytesIO();book=canvas.Canvas(output)
    for page in range(2):
        book.drawString(40,700,f'Synthetic page {page+1}');book.showPage()
    book.save();return output.getvalue()


def candidate(page,weight='3170.4'):
    return dict(page=page,text='수출신고필증 AWB',rows=[dict(number='00001X',line='001',version='1',kg=weight)],evidence=[],warning='')


def test_checkpoint_resume_review_stale_and_scope(setup,monkeypatch):
    app,client,base,*_=setup
    evidence=client.post(base+'/evidence?name=test.pdf',content=pdf()).json()['id']
    calls=[]
    def extract(content,page):
        calls.append(page);return candidate(page)
    monkeypatch.setattr(review,'extract_page',extract)
    body=dict(evidence_id=evidence,start=1,count=1)
    first=client.post(base+'/pdf-process',json=body)
    assert first.status_code==200,first.text
    assert first.json()['processed']==1 and first.json()['pages']==2
    assert client.post(base+'/pdf-process',json=body).status_code==200 and calls==[1]
    record=dict(evidence_id=evidence,page=1,extraction_hash=first.json()['page_states'][0]['extraction_hash'],state='LOCALLY_REVIEWED',reason='가상 원본 대조')
    assert client.post(base+'/pdf-page-review',json=record).status_code==200
    assert client.get(base+f'/pdf-status?evidence_id={evidence}').json()['page_states'][0]['state']=='LOCALLY_REVIEWED'
    monkeypatch.setattr(review,'extract_page',lambda content,page:candidate(page,'3171'))
    refreshed=client.post(base+'/pdf-process',json=dict(body,force=True)).json()
    assert refreshed['page_states'][0]['state']=='STALE_REVIEW'
    assert client.post(base+'/pdf-page-review',json=record).status_code==409
    assert client.post(base+'/pdf-process',json=dict(body,count=6)).status_code==422
    assert client.get('/api/org/2/sorting/pdf-status',params=dict(evidence_id=evidence)).status_code==404
    image=client.get(base+'/pdf-image',params=dict(evidence_id=evidence,page=2))
    assert image.status_code==200 and image.content.startswith(b'\x89PNG')
    assert client.get(base+'/pdf-image',params=dict(evidence_id=evidence,page=3)).status_code==422
    invalid=client.post(base+'/evidence?name=invalid.pdf',content=b'not a PDF').json()['id']
    assert client.get(base+'/pdf-status',params=dict(evidence_id=invalid)).status_code==422
    assert all(r['kind']!=review.CACHE for r in client.get(base+'/records').json())
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'changed'))
    assert client.post(base+'/pdf-process',json=body).status_code==409


def test_pdf_excel_difference_is_candidate_only(setup,monkeypatch):
    app,client,base,*_=setup
    evidence=client.post(base+'/evidence?name=test.pdf',content=pdf()).json()['id']
    excel=client.post(base+'/evidence?name=test.xlsx',content=source([['00001','3170','가상품목',None],['002X','0','가상품목','1']])).json()['id']
    monkeypatch.setattr(review,'extract_page',lambda content,page:candidate(page))
    client.post(base+'/pdf-process',json=dict(evidence_id=evidence,start=1,count=2))
    result=client.post(base+'/pdf-excel-preview',json=dict(pdf_id=evidence,excel_id=excel,sheet='Sheet'))
    assert result.status_code==200,result.text
    value=result.json();row=value['rows'][0]
    assert row['difference']=='0.4' and row['weight_status']=='DIFFERENCE'
    assert not row['physical_export_verified'] and row['item_line_status']=='UNVERIFIED'
    assert value['payable_amount'] is None and not value['automatic_approval']
    assert value['rows'][1]['weight_status']=='UNKNOWN'
    assert len(value['pdf']['groups'])==1 and value['pdf']['groups'][0]['net_weight']=='3170.4'
    assert client.get(base+'/claims').json()==[]


def test_classifier_numeric_equivalence_transport_and_version_conflicts():
    first=candidate(1,'10.0');second=candidate(2,'10.00')
    result=documents.group_pages([first,second])
    assert result['documents'][0]['kind']=='DECLARATION'
    assert len(result['groups'])==1 and not result['groups'][0]['conflict']
    assert result['groups'][0]['net_weight']=='10.0'
    transport=dict(candidate(3,'100'),text='AIR WAYBILL')
    assert documents.group_pages([transport])['groups']==[]
    assert documents.group_pages([first,candidate(2,'11')])['groups'][0]['conflict']


def test_real_page_extraction_preserves_original_page():
    result=review.extract_page(pdf(),2)
    assert result['page']==2 and 'Synthetic page 2' in result['text']
    with pytest.raises(ValueError):
        review.extract_page(pdf(),3)
