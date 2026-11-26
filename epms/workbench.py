"""Task-oriented monthly overview and reusable office/import preferences."""
from io import BytesIO
from datetime import datetime,date
import csv,re
from hashlib import sha256
from sqlalchemy import Column,Integer,JSON,LargeBinary,String,UniqueConstraint,select
from fastapi import Request
from pydantic import Field
from epms import sorting as s,store,settlement as st,rules,monthly_report as mr

settings=s.table('workspace_settings',Column('revision',Integer,nullable=False),Column('data',JSON,nullable=False),UniqueConstraint('org'))
imports=s.table('workspace_imports',Column('sha',String,nullable=False),Column('name',String,nullable=False),Column('content',LargeBinary,nullable=False),Column('data',JSON,nullable=False),UniqueConstraint('org','sha'))
TABLES=(settings,imports)
class Preferences(st.Strict):
    revision:int|None=None
    writer:str=Field(default='',max_length=100)
    destination:str=Field(default='',max_length=200)
    mapping:dict[str,int]=Field(default_factory=dict,max_length=20)
    mapping_headers:list[str]=Field(default_factory=list,max_length=40)
class ImportPreview(st.Strict):
    mapping:dict[str,int]=Field(default_factory=dict,max_length=20)
    overrides:dict[int,dict[str,str]]=Field(default_factory=dict,max_length=2000)
    include_rows:list[int]|None=Field(default=None,max_length=2000)

def preferences(conn,org):
    row=conn.execute(settings.select().where(settings.c.org==org)).mappings().first()
    return dict(row) if row else dict(revision=None,data=dict(writer='',destination='',mapping={},mapping_headers=[]))

def overview(conn,org,month):
    from calendar import monthrange
    from epms import sorting_receipts as intake,support_budget as budget
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):st.fail('업무 월을 확인하세요.')
    y,m=map(int,month.split('-'))
    if not 1900<=y<=9999:st.fail('업무 연도를 확인하세요.')
    start=month+'-01';end=month+f'-{monthrange(y,m)[1]:02}'
    overlap=lambda d:bool(d.get('start') and d.get('end') and d['start']<=end and d['end']>=start)
    batches=s.list_rows(conn,s.batches,org);expenses=s.list_rows(conn,s.expenses,org)
    receipts=[intake.summary(conn,org,r,batches,expenses) for r in s.list_rows(conn,intake.receipts,org) if overlap(r['data'])]
    jobs=[r for r in s.list_rows(conn,st.jobs,org) if r['data']['year']==y and overlap(r['data'])]
    reports=[dict(id=r['id'],revision=r['revision'],state=r['state'],previous=r['previous'],data=r['data']) for r in s.list_rows(conn,mr.reports,org) if r['data']['year']==y and r['data']['month']==m]
    plan=budget.plan(conn,org,y)
    return dict(month=month,start=start,end=end,receipts=receipts,jobs=jobs,reports=reports,plan=None if not plan else dict(revision=plan['revision'],title=plan['data']['title'],enabled=plan['data']['enabled'],status=plan['data']['status']),preferences=preferences(conn,org),facilities=[dict(id=f['id'],name=f['data']['name']) for f in s.list_rows(conn,s.facilities,org)])

def parse_upload(name,raw):
    if len(raw)>10_000_000:st.fail('명세 파일은 10MB 이내로 등록하세요.',413)
    if name.lower().endswith('.xlsx'):
        from zipfile import ZipFile,BadZipFile
        from openpyxl import load_workbook
        try:
            with ZipFile(BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist())>60_000_000:st.fail('압축 해제 용량이 너무 큰 명세 파일입니다.')
            wb=load_workbook(BytesIO(raw),read_only=True,data_only=True)
            try:
                ws=wb.active
                if ws.max_column>40 or ws.max_row>2001:st.fail('명세는 첫 시트 기준 40열·2000행 이내로 나누어 등록하세요.')
                rows=[]
                for row in ws.iter_rows():
                    rows.append([v.value.isoformat()[:10] if isinstance(v.value,(datetime,date)) else str(v.value if v.value is not None else '') for v in row])
                    if len(rows)>2001:st.fail('명세는 2000행 이내로 등록하세요.')
            finally:wb.close()
        except (ValueError,KeyError,BadZipFile,OSError):st.fail('열 수 없는 Excel 파일입니다. .xlsx 명세를 확인하세요.')
    elif name.lower().endswith('.csv'):
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:text=raw.decode('cp949')
            except UnicodeDecodeError:st.fail('CSV 문자 인코딩을 확인하세요. UTF-8 또는 CP949를 지원합니다.')
        rows=[]
        for row in csv.reader(text.splitlines()):
            if len(row)>40 or len(rows)>2000:st.fail('명세는 40열·2000행 이내로 나누어 등록하세요.')
            rows.append(row)
    else:st.fail('Excel(.xlsx) 또는 CSV 지급 명세를 등록하세요.')
    if len(rows)<2 or not any(v.strip() for v in rows[0]):st.fail('첫 행이 열 제목인 명세 파일을 사용하세요.')
    width=len(rows[0])
    if any(len(row)>width and any(str(v).strip() for v in row[width:]) for row in rows[1:]):st.fail('제목 없는 추가 열이 있습니다. 첫 행의 열 제목을 확인하세요.')
    selected=[(i,row) for i,row in enumerate(rows[1:],2) if any(str(v).strip() for v in row)]
    values=[(row+['']*width)[:width] for _,row in selected]
    if any(len(v)>2000 for row in values for v in row):st.fail('명세 셀은 2000자 이내로 입력하세요.')
    return dict(headers=rows[0],rows=values,row_numbers=[i for i,_ in selected])

def import_preview(conn,org,row,value):
    from epms.support_budget import won
    members=store.records(conn,store.memberships,org);index={rules.business_number(m['number']):m for m in members};byid={m['id']:m for m in members};mapping=value.mapping
    fields={'number','name','date','reference','recipient','amount','kind','decision','resolution','resolution_date','resolution_amount','review_date','reviewer'}
    if any(k not in fields or not 0<=v<len(row['data']['headers']) for k,v in mapping.items()):st.fail('명세 열 대응을 확인하세요.')
    result=[];groups={};errors=[]
    numbers=row['data'].get('row_numbers',list(range(2,len(row['data']['rows'])+2)))
    if value.include_rows is not None and (not value.include_rows or not set(value.include_rows)<=set(numbers)):st.fail('가져올 원본 행을 선택하세요.')
    for i,raw in zip(numbers,row['data']['rows']):
        if value.include_rows is not None and i not in value.include_rows:continue
        override=value.overrides.get(i,{})
        def cell(key):return str(override.get(key,raw[mapping[key]] if key in mapping else '')).strip()
        number=rules.business_number(cell('number'));member=index.get(number) if rules.valid_number(number) else None
        if override.get('member_id'):
            try:member=byid.get(int(override['member_id']))
            except ValueError:member=None
        issues=[]
        if not member:issues.append('사업자번호로 회원사를 확인하거나 직접 선택하세요.')
        amount_text=re.sub(r'^\s*[₩￦]\s*','',cell('amount'));amount_text=re.sub(r'\s*원\s*$','',amount_text).strip()
        refund=cell('kind') in ('반환','환수','환급')
        if amount_text.startswith('-'):refund=True;amount_text=amount_text[1:]
        try:
            if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.0+)?',amount_text):raise ValueError('invalid won')
            amount=won(amount_text.replace(',',''))
        except Exception:amount=None;issues.append('실제 지급액을 원 단위로 확인하세요.')
        day=re.sub(r'[./]','-',cell('date')).strip()
        try:day=date.fromisoformat(day).isoformat()
        except ValueError:issues.append('지급일을 YYYY-MM-DD로 확인하세요.')
        reference=cell('reference');recipient=cell('recipient')
        if not reference:issues.append('중복 점검에 사용할 거래 고유번호를 입력하세요.')
        if not recipient:issues.append('명세 원문의 실제 수취인을 확인하세요.')
        proposed=dict(row=i,member_id=member['id'] if member else None,name=member['name'] if member else cell('name'),number=member['number'] if member else number,date=day,reference=reference,recipient=recipient,amount=None if amount is None else str(amount),refund=refund,issues=issues,candidates=[dict(id=m['id'],name=m['name'],number=m['number']) for m in members if cell('name') and cell('name') in m['name']][:10])
        result.append(proposed)
        if issues:errors.append(f'{i}행: '+' / '.join(issues));continue
        key=(reference.casefold(),day,refund)
        group=groups.setdefault(key,dict(key='import-'+str(row['id'])+'-'+str(i),reference=reference,date=day,recipient=recipient,recipient_reason='',refund=refund,amount='0',allocations=[],evidence=[],import_source=row['id'],import_rows=[],recipients=[]))
        group['amount']=str(won(group['amount'])+amount);group['import_rows'].append(i);group['recipients'].append(recipient)
        allocation=next((a for a in group['allocations'] if a['member_id']==member['id']),None)
        if allocation:allocation['amount']=str(won(allocation['amount'])+amount)
        else:group['allocations'].append(dict(member_id=member['id'],amount=str(amount)))
        if recipient!=member['name']:group['recipient_reason']='원본 명세의 회원사·수취인 대응을 사무국에서 확인: '+', '.join(dict.fromkeys(group['recipients']))
    payments=[]
    for group in groups.values():
        recipients=group.pop('recipients')
        if len(set(recipients))>1:group['recipient']='회원사별 일괄 지급 명세';group['recipient_reason']='원본 명세의 수취인별 배분 대조: '+', '.join(dict.fromkeys(recipients))
        payments.append(group)
    return dict(source=row['id'],name=row['name'],sha=row['sha'],rows=result,payments=payments,errors=errors)

def install(app,db,gate,actor):
    path='/api/org/{org}/workspace'
    @app.get(path)
    def get(org:int,month:str):
        with db.connect() as conn:gate(conn,org);return overview(conn,org,month)
    @app.get(path+'/preferences')
    def prefs(org:int):
        with db.connect() as conn:gate(conn,org);return preferences(conn,org)
    @app.put(path+'/preferences')
    def save(org:int,value:Preferences,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);old=preferences(conn,org)
            if value.revision!=old['revision']:st.fail('업무 기본정보가 변경되었습니다. 다시 조회하세요.',409)
            data=value.model_dump(exclude={'revision'});revision=(old['revision'] or 0)+1
            if old.get('id'):conn.execute(settings.update().where(settings.c.id==old['id']).values(data=data,revision=revision))
            else:conn.execute(settings.insert().values(org=org,data=data,revision=revision))
            store.log(conn,org,actor(request),'workspace_settings',org,'업무 기본정보 저장',old['data'],data);return preferences(conn,org)
    @app.post(path+'/imports/upload')
    async def upload(org:int,name:str,request:Request):
        if len(name)>240 or any(v in name for v in '/\\:\r\n'):st.fail('명세 파일명을 확인하세요.')
        with db.connect() as conn:gate(conn,org)
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>10_000_000:st.fail('명세 파일은 10MB 이내입니다.',413)
            chunks.append(chunk)
        raw=b''.join(chunks);parsed=parse_upload(name,raw);digest=sha256(raw).hexdigest()
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            old=conn.execute(imports.select().where(imports.c.org==org,imports.c.sha==digest)).mappings().first()
            key=old['id'] if old else conn.execute(imports.insert().values(org=org,sha=digest,name=name,content=raw,data=dict(parsed,created=store.now()))).inserted_primary_key[0]
            store.log(conn,org,actor(request),'payment_statement',key,'지급 명세 원본 등록',None,dict(name=name,sha=digest))
            return dict(id=key,sha=digest,name=name,**parsed)
    @app.post(path+'/imports/{key}/preview')
    def preview(org:int,key:int,value:ImportPreview):
        with db.connect() as conn:gate(conn,org);return import_preview(conn,org,s.scoped(conn,imports,org,key),value)
