"""Explicit CSV mapping into append-only intake records; no vendor format assumptions."""
import csv,hashlib
from io import StringIO
from datetime import date
from epms import sorting as s,sorting_production as production,store

VERSION='intake-csv-1'
REQUIRED={'external_id','producer_id','product','date','quantity','unit'}
OPTIONAL={'variety','color','grade','note'}


def read(conn,org,evidence_id,encoding):
    file=s.scoped(conn,s.evidence,org,evidence_id)
    if hashlib.sha256(file['content']).hexdigest()!=file['sha']:
        s.reject('SORT_E08','evidence','원본 내용과 해시가 다릅니다.')
    if encoding not in {'utf-8-sig','cp949'} or len(file['content'])>20_000_000:
        s.reject('SORT_E09','encoding','지원 인코딩 또는 20MB 제한을 확인하세요.',422)
    try:
        text=file['content'].decode(encoding)
        if '\x00' in text:
            raise ValueError()
        reader=csv.reader(StringIO(text,newline=''),strict=True)
        rows=[]
        for row in reader:
            if len(row)>128 or len(rows)>5000:
                raise ValueError()
            rows.append(row)
    except (UnicodeError,csv.Error,ValueError):
        s.reject('SORT_E09','file','CSV 인코딩·열 수(128)·행 수(5,000)를 확인하세요.',422)
    if not rows or not rows[0] or any(not str(h).strip() for h in rows[0]) or len(set(rows[0]))!=len(rows[0]):
        s.reject('SORT_E09','header','비어 있거나 중복된 CSV 머리글은 사용할 수 없습니다.',422)
    return file,rows[0],rows[1:]


def columns(conn,org,evidence_id,encoding):
    file,headers,rows=read(conn,org,evidence_id,encoding)
    return dict(sha=file['sha'],headers=headers,row_count=len(rows),version=VERSION)


def preview(conn,org,evidence_id,encoding,facility_id,system,mapping):
    system=system.strip()
    facility=s.scoped(conn,s.facilities,org,facility_id)
    file,headers,raw=read(conn,org,evidence_id,encoding)
    if not system.strip() or len(system)>30 or ':' in system:
        s.reject('SORT_E09','system','원본 시스템명을 30자 이내로 입력하세요. 콜론은 사용할 수 없습니다.',422)
    if not REQUIRED.issubset(mapping) or set(mapping)-(REQUIRED|OPTIONAL) or any(type(i)!=int or not 0<=i<len(headers) for i in mapping.values()) or len(set(mapping.values()))!=len(mapping):
        s.reject('SORT_E09','mapping','필수 열을 빠짐없이 서로 다른 원본 열에 매핑하세요.',422)
    config=dict(version=VERSION,encoding=encoding,facility_id=facility_id,system=system,mapping=mapping)
    rows=[];seen={}
    for number,cells in enumerate(raw,2):
        if not any(str(c).strip() for c in cells):
            continue
        original={field:cells[index] if index<len(cells) else '' for field,index in mapping.items()}
        values={field:value.strip() for field,value in original.items()}
        issues=[]
        if len(cells)!=len(headers):
            issues.append('머리글과 열 수가 다릅니다.')
        if any(not values[k] for k in REQUIRED):
            issues.append('필수 원본값이 비어 있습니다.')
        if any(values[k].startswith(('=','+','@')) for k in REQUIRED):
            issues.append('필수값의 수식형 문자열은 자동 등록하지 않습니다.')
        if any(len(v)>100 for k,v in values.items() if k!='note') or len(values.get('note',''))>500:
            issues.append('원본값 길이 제한을 초과했습니다.')
        identifier=system+':'+values['external_id']
        if len(identifier)>100:
            issues.append('시스템명을 포함한 원본번호는 100자 이하여야 합니다.')
        if values['product']!=facility['data']['product']:
            issues.append('시설 품목과 원본 품목이 다릅니다.')
        if values['unit']!='kg':
            issues.append('검증된 kg 단위만 등록할 수 있습니다.')
        try:
            if date.fromisoformat(values['date']).isoformat()!=values['date']:
                raise ValueError()
        except ValueError:
            issues.append('입고일은 유효한 YYYY-MM-DD 형식이어야 합니다.')
        try:
            if s.kg(values['quantity'])<=0:
                issues.append('입고량은 0보다 커야 합니다.')
        except s.SortingError:
            issues.append('입고량의 십진수 형식·정밀도·범위를 확인하세요.')
        entry=dict(row=number,original=original,cells=cells,values=dict(values,external_id=identifier),issues=issues)
        rows.append(entry)
        seen.setdefault(identifier,[]).append(entry)
    for entries in seen.values():
        if len(entries)>1:
            for entry in entries:
                entry['issues'].append('파일 내 입고 원본번호 중복: 순서로 합치지 않습니다.')
    preview_hash=s.digest(dict(sha=file['sha'],config=config,rows=rows))
    return dict(evidence_id=evidence_id,sha=file['sha'],headers=headers,config=config,mapping_hash=s.digest(config),
        preview_hash=preview_hash,rows=rows,valid_rows=sum(not r['issues'] for r in rows),automatic_approval=False)


def commit(conn,org,data,actor):
    view=preview(conn,org,**{key:data[key] for key in ('evidence_id','encoding','facility_id','system','mapping')})
    if data['preview_hash']!=view['preview_hash']:
        s.reject('SORT_E08','preview_hash','미리보기와 원본·매핑이 다릅니다. 다시 조회하세요.')
    payload_hash=s.digest(data)
    for record in production.production_rows(conn,org,'ERP_INTAKE_IMPORT'):
        if record['data']['request_key']==data['request_key']:
            if record['data']['payload_hash']!=payload_hash:
                s.reject('SORT_E09','request_key','같은 요청키의 내용이 다릅니다.')
            ids=record['data']['intake_ids']
            return dict(id=record['id'],intake_ids=ids,reused=True,created_count=0,reused_count=len(ids))
    selected=data['rows']
    by_number={r['row']:r for r in view['rows']}
    if not 1<=len(selected)<=1000 or len(set(selected))!=len(selected) or any(n not in by_number or by_number[n]['issues'] for n in selected):
        s.reject('SORT_E09','rows','오류 없는 원본 행을 중복 없이 1~1,000개 선택하세요.',422)
    ids=[]
    known=set(conn.execute(s.records.select().with_only_columns(s.records.c.id).where(s.records.c.org==org,s.records.c.kind=='INTAKE')).scalars())
    for number in selected:
        row=by_number[number]
        values=dict(row['values'],facility_id=data['facility_id'],evidence_id=data['evidence_id'],source=dict(
            sha=view['sha'],row=number,original=row['original'],cells=row['cells'],config=view['config'],mapping_hash=view['mapping_hash']))
        ids.append(production.intake(conn,org,values,actor))
    created=sum(entity not in known for entity in ids)
    value=dict(request_key=data['request_key'],payload_hash=payload_hash,evidence_id=data['evidence_id'],sha=view['sha'],created_count=created,reused_count=len(ids)-created,
        config=view['config'],mapping_hash=view['mapping_hash'],preview_hash=view['preview_hash'],rows=selected,intake_ids=ids,actor=actor,time=store.now())
    entity=conn.execute(s.records.insert().values(org=org,kind='ERP_INTAKE_IMPORT',revision=1,data=value)).inserted_primary_key[0]
    store.log(conn,org,actor,'sorting_intake_import',entity,'선택한 CSV 입고행 등록',None,dict(rows=len(ids),mapping_hash=view['mapping_hash']))
    return dict(id=entity,intake_ids=ids,reused=False,created_count=created,reused_count=len(ids)-created)


def history(conn,org):
    return [dict(id=r['id'],time=r['data']['time'],evidence_id=r['data']['evidence_id'],system=r['data']['config']['system'],
        facility_id=r['data']['config']['facility_id'],version=r['data']['config']['version'],mapping_hash=r['data']['mapping_hash'],
        row_count=len(r['data']['rows']),created_count=r['data']['created_count'],reused_count=r['data']['reused_count'])
        for r in reversed(production.production_rows(conn,org,'ERP_INTAKE_IMPORT')[-100:])]
