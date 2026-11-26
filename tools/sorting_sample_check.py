"""Aggregate private local samples without exporting source identifiers."""
import json
from decimal import Decimal
from pathlib import Path
from openpyxl import load_workbook
import hashlib
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from epms import sorting_books,sorting_reconciliation
results=[]
for file in (root/'sample2').rglob('*.xlsx'):
    book=load_workbook(file,read_only=True,data_only=True,keep_links=False)
    sheet=book.worksheets[0]
    if sheet.title=='수출실적 리스트':
        quantities=[Decimal(str(row[14])) for row in sheet.iter_rows(min_row=2,values_only=True) if row[7] and row[14] is not None]
        expected='126524'
        kind='export-list'
    else:
        quantities=[]
        for row in sheet.iter_rows(min_row=5,values_only=True):
            if any(''.join(str(v or '').split()) in {'합계','총계','소계'} for v in row):
                continue
            if len(row)>5 and isinstance(row[4],str) and '-' in row[4] and isinstance(row[5],(int,float)):
                quantities.append(Decimal(str(row[5])))
        expected='126524'
        kind='sorting-detail'
    total=sum(quantities,Decimal(0))
    results.append(dict(profile=kind,rows=len(quantities),quantity=str(total),expected=expected,
                        result='PASS' if total==Decimal(expected) and len(quantities)==112 else 'MISMATCH'))
    book.close()
out=root/'output'/'sorting-private'/'sample-check.json'
out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
files=sorted((root/'sample2').rglob('*.xlsx'))
if len(files)==2:
    previews=[]
    for file in files:
        content=file.read_bytes()
        metadata=sorting_books.preview(content)
        previews.append(sorting_books.preview(content,metadata['sheets'][0]))
    paired=sorting_reconciliation.compare(*previews)
    paired['sources']=[dict(source=str(f.relative_to(root/'sample2')),sha=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files]
    (root/'output/sorting-private/paired-table-reconciliation.json').write_text(json.dumps(paired,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Source-table comparison:',json.dumps(dict(left=paired['left'],right=paired['right'],summary=paired['summary'],quantity_summary=paired['quantity_summary']),ensure_ascii=False))
inventory_path=root/'output/sorting-private/inventory.json'
if inventory_path.exists():
    inventory=json.loads(inventory_path.read_text(encoding='utf-8'))
    pdfs=[r for r in inventory if r['source'].endswith('.pdf')]
    coverage=dict(pdf_files=len(pdfs),unique_pdf_hashes=len({r['sha256'] for r in pdfs}),
        pages=sum(r.get('pages',0) for r in pdfs),text_pages=sum(r.get('text_pages',0) for r in pdfs),
        hwp_unparsed=sum(r['source'].endswith('.hwp') for r in inventory),
        original_pdf_full_reconciliation='NOT_VERIFIED',inventory_current=False)
    unique_pdfs={r['sha256']:r for r in pdfs}
    coverage['unique_pdf_pages']=sum(r.get('pages',0) for r in unique_pdfs.values())
    coverage['pages_without_text']=coverage['pages']-coverage['text_pages']
    coverage['inventory_current']=all((root/'sample2'/r['source']).resolve().is_relative_to((root/'sample2').resolve()) and hashlib.sha256((root/'sample2'/r['source']).read_bytes()).hexdigest()==r['sha256'] for r in inventory)
    (root/'output/sorting-private/coverage.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
    print('Source coverage:',json.dumps(coverage))
