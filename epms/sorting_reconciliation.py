"""Read-only comparison of two source tables; never confirms physical exports."""
import csv
import hashlib
from collections import defaultdict,Counter
from decimal import Decimal
from io import StringIO
from epms import sorting as s,sorting_books


def compare(left,right):
    indexes=[]
    totals=[]
    for source in (left,right):
        index=defaultdict(list)
        total=Decimal(0)
        invalid=0
        for row in source['rows']:
            original=row['original']
            number=s.normalize_number(original.get('number'))
            item=str(original.get('line','')).strip()
            if item.isdigit():
                item=str(int(item))
            value=None
            try:
                value=s.decimal(original.get('quantity',''),'quantity')
            except s.SortingError:
                invalid+=1
            if value is not None:
                total+=value
            index[number].append(dict(row=row['row'],sheet=row['sheet'],cells=row['cells'],original=original,item=item,quantity=None if value is None else str(value)))
        indexes.append(index)
        totals.append(dict(rows=len(source['rows']),literal_quantity=str(total),unverified_quantity_rows=invalid,complete_quantity_total=str(total) if invalid==0 and source['rows'] else None))
    result=[]
    for number in sorted(indexes[0].keys()|indexes[1].keys()):
        left_rows,right_rows=indexes[0].get(number,[]),indexes[1].get(number,[])
        # An empty number is never a match, and repeated numbers require item-level selection.
        if not number or len(left_rows)>1 or len(right_rows)>1:
            result.append(dict(number=number,status='AMBIGUOUS',quantity_status='UNKNOWN',difference=None,left=left_rows,right=right_rows,issues=['신고번호 누락 또는 복수 행: 자동 대응 금지']))
            continue
        if not left_rows or not right_rows:
            result.append(dict(number=number,status='LEFT_ONLY' if left_rows else 'RIGHT_ONLY',quantity_status='UNKNOWN',difference=None,left=left_rows,right=right_rows,issues=['상대 자료에 대응 행 없음']))
            continue
        a,b=left_rows[0],right_rows[0]
        issues=[]
        line_status='ITEM_LINE_UNVERIFIED' if not a['item'] or not b['item'] else 'ITEM_DIFFERENCE' if a['item']!=b['item'] else 'TABLE_REFERENCE_MATCH'
        if line_status!='TABLE_REFERENCE_MATCH':
            issues.append('품목란 미확인 또는 품목란 차이: 원본 대조 필요')
        if a['original'].get('product') and b['original'].get('product') and a['original']['product']!=b['original']['product']:
            issues.append('품명 원문 차이: 임의 품목 동등성 적용 금지')
        elif not a['original'].get('product') or not b['original'].get('product'):
            issues.append('품목 미기재: 별도 원본 확인 필요')
        if a['quantity'] is None or b['quantity'] is None:
            quantity_status,difference='UNKNOWN',None
            issues.append('수량 공란·수식·형식 미확인: 0으로 대체하지 않음')
        else:
            difference=str(Decimal(a['quantity'])-Decimal(b['quantity']))
            quantity_status='MATCH' if Decimal(difference)==0 else 'DIFFERENCE'
        result.append(dict(number=number,status=line_status,quantity_status=quantity_status,difference=difference,left=left_rows,right=right_rows,issues=issues))
    return dict(label='표 간 대사 / 신고 원본·실물·지원 적격 미검증',left=totals[0],right=totals[1],
        summary=dict(Counter(row['status'] for row in result)),quantity_summary=dict(Counter(row['quantity_status'] for row in result)),rows=result,
        automatic_matching=False,external_duplicate_check='UNKNOWN',approved_amount=None,payable_amount=None)


def from_evidence(conn,org,left_id,left_sheet,right_id,right_sheet):
    files=[s.scoped(conn,s.evidence,org,i) for i in (left_id,right_id)]
    if any(hashlib.sha256(f['content']).hexdigest()!=f['sha'] for f in files):
        s.reject('SORT_E08','evidence','대사 원본의 저장 해시와 파일 내용이 다릅니다.')
    result=compare(sorting_books.preview(files[0]['content'],left_sheet),sorting_books.preview(files[1]['content'],right_sheet))
    result['sources']=[dict(id=f['id'],sha=f['sha'],sheet=sheet) for f,sheet in zip(files,(left_sheet,right_sheet))]
    result['same_source']=files[0]['sha']==files[1]['sha']
    result['version']='table-reconcile-1'
    result['comparison_hash']=s.digest(dict(sources=result['sources'],summary=result['summary'],rows=result['rows']))
    return result


def csv_report(result):
    output=StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow([result['label'],'검토용 / 지급 미확정'])
    writer.writerow(['대사 hash',literal(result['comparison_hash'])])
    for side,source in zip(('왼쪽','오른쪽'),result['sources']):
        writer.writerow([side+' 원본 hash',literal(source['sha']),'시트',literal(source['sheet'])])
    writer.writerow(['신고번호 검색키','대응 상태','중량 비교','왼쪽 행','오른쪽 행','왼쪽 원본량','오른쪽 원본량','차이 kg','보완'])
    for row in result['rows']:
        writer.writerow([literal(row['number']),row['status'],row['quantity_status'],
            ','.join(str(r['row']) for r in row['left']),','.join(str(r['row']) for r in row['right']),
            literal(' | '.join(r['original'].get('quantity','') for r in row['left'])),literal(' | '.join(r['original'].get('quantity','') for r in row['right'])),
            literal(row['difference'] if row['difference'] is not None else ''),safe(' / '.join(row['issues']))])
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def safe(value):
    value=str(value)
    return "'"+value if value and (value[:1] in '=+-@\t\r\n' or bool(value.lstrip()) and value.lstrip()[:1] in '=+-@') else value


def literal(value):
    return "'"+str(value) if value!='' else ''
