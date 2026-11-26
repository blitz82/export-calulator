from io import BytesIO
import csv
from openpyxl import Workbook
from epms import sorting_books as books,sorting_reconciliation as reconcile,sorting as s
from test_sorting import setup,payload


def source(rows):
    book=Workbook()
    sheet=book.active
    sheet.append(['신고번호','순중량(kg)','품목','품목란'])
    for row in rows:
        sheet.append(row)
    return books.workbook_bytes(book)


def test_exact_difference_null_zero_duplicate_and_missing():
    left=source([['00-001X','3170.4','가상품목','001'],['002X','0','가상품목','001'],['003X',None,'가상품목','001'],['004X','10','가상품목','001'],['004X','20','가상품목','002'],['005X','1','가상품목','001']])
    right=source([['00001X','3170','가상품목','1'],['002X','0','가상품목','1'],['003X','1','가상품목','1'],['004X','30','가상품목','1'],['006X','1','가상품목','1']])
    result=reconcile.compare(books.preview(left,'Sheet'),books.preview(right,'Sheet'))
    rows={r['number']:r for r in result['rows']}
    assert rows['00001X']['difference']=='0.4'
    assert rows['002X']['difference']=='0' and rows['002X']['quantity_status']=='MATCH'
    assert rows['003X']['quantity_status']=='UNKNOWN'
    assert rows['004X']['status']=='AMBIGUOUS'
    assert rows['005X']['status']=='LEFT_ONLY' and rows['006X']['status']=='RIGHT_ONLY'
    assert result['left']['complete_quantity_total'] is None
    assert not result['automatic_matching'] and result['payable_amount'] is None


def test_item_unknown_product_difference_and_formula_are_not_verified():
    left=books.preview(source([['001X','=100*2','별칭품목',None]]),'Sheet')
    right=books.preview(source([['001X','200','가상품목',None]]),'Sheet')
    result=reconcile.compare(left,right)
    assert result['rows'][0]['status']=='ITEM_LINE_UNVERIFIED'
    assert result['rows'][0]['quantity_status']=='UNKNOWN'
    assert len(result['rows'][0]['issues'])>=2
    assert result['left']['literal_quantity']=='0' and result['left']['complete_quantity_total'] is None


def test_reconciliation_api_is_read_only_scoped_and_csv_safe(setup):
    app,client,base,*_=setup
    same_file=source([['=1+1','100','가상품목','1']])
    left=client.post(base+'/evidence?name=left.xlsx',content=same_file).json()['id']
    right=client.post(base+'/evidence?name=right.xlsx',content=same_file).json()['id']
    data=dict(left_id=left,left_sheet='Sheet',right_id=right,right_sheet='Sheet')
    before=client.get(base+'/claims').json()
    result=client.post(base+'/reconcile-preview',json=data)
    assert result.status_code==200,result.text
    assert result.json()['same_source']
    assert client.get(base+'/claims').json()==before
    report=client.post(base+'/reconcile-report',json=data)
    assert report.status_code==200
    rows=list(csv.reader(report.content.decode('utf-8-sig').splitlines()))
    assert rows[-1][0].startswith("'=")
    assert client.post('/api/org/2/sorting/reconcile-preview',json=data).status_code==404
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.update().where(s.evidence.c.id==left).values(content=b'changed'))
    assert client.post(base+'/reconcile-preview',json=data).status_code==409


def test_print_form_escapes_input_keeps_unknowns_and_blocks_official(setup):
    app,client,base,evidence,facility,batch=setup
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    with app.state.db.begin() as conn:
        row=s.scoped(conn,s.claims,1,claim)
        conn.execute(s.claims.update().where(s.claims.c.id==claim).values(data=dict(row['data'],product='<script>alert(1)</script>')))
    response=client.get(f'{base}/claims/{claim}/print')
    assert response.status_code==200
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert 'A4 portrait' in response.text and '미확인' in response.text and '공식 제출용 아님' in response.text
    assert '사무국 발급 확인서 아님' in response.text
    assert client.get(f'{base}/claims/{claim}/print?official=true').status_code==403
    assert client.get(f'/api/org/2/sorting/claims/{claim}/print').status_code==404
