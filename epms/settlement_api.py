from contextlib import closing
from hashlib import sha256
import re
import os
from fastapi import Request,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from epms import settlement as st,sorting as s,store,ocr,rules,contract_gate

class Action(BaseModel):
    revision:int
    reason:str=Field(min_length=1,max_length=1000)

def install(app,db,organization,actor):
    prefix='/api/org/{org}/settlements'
    def gate(conn,org):
        if not app.state.sorting_enabled:raise HTTPException(404,'공동선별비 기능이 꺼져 있습니다.')
        organization(conn,org)

    @app.get(prefix+'/catalog')
    def catalog(org:int,job_id:int|None=None,records_only:bool=False):
        from epms.sorting_production import batch_committed
        from epms import sorting_receipts as intake
        with db.connect() as conn:
            gate(conn,org)
            if job_id:s.scoped(conn,st.jobs,org,job_id)
            batches=[];declarations=[];expenses=[];index={rules.business_number(m['number']):m for m in store.records(conn,store.memberships,org)}
            agreements=contract_gate.context(conn,org)
            for row in s.list_rows(conn,s.batches,org):
                available=row['quantity']-batch_committed(conn,org,row['id'])+st.used_batch(conn,row['id'])-st.used_batch(conn,row['id'],job_id)
                root_id,group_available=st.group_remaining(conn,org,row['id'],job_id)
                available=min(available,group_available)
                issues=st.source_issues(row['data'])
                if intake.blocked(conn,org,row['data']):issues.append('접수 묶음 보완 필요')
                batches.append(dict(row,quantity=s.text_quantity(row['quantity']),available=s.text_quantity(available),pool_id=root_id,pool_available=s.text_quantity(group_available),ready=not issues and row['quantity']>0,issues=issues,affected_jobs=intake.affected(conn,org,'source',row['id'])))
            for row in ([] if records_only else store.records(conn,store.declarations,org)):
                errors=[]
                try:
                    _,key,total=s.physical(conn,org,row['id'],row['revision'])
                    legacy=sum(conn.execute(select(s.allocations.c.quantity).where(s.allocations.c.logical_key==key,s.allocations.c.active==1)).scalars())
                    available=total-legacy-st.used_declaration(conn,key,job_id)
                    if available<0:errors.append('기존 연결량이 수정된 신고 순중량을 초과했습니다. 원본과 연결을 다시 확인하세요.')
                except s.SortingError as e:errors.append(str(e));available=0
                errors.extend(p['message'] for p in rules.validate_line(row,index)[0]+contract_gate.evaluate(row,index,agreements)[0] if p['severity']!='INFO')
                pdf=None
                if row.get('source'):
                    source=conn.execute(select(store.import_rows.c.file,store.import_rows.c.row,store.import_files.c.name,store.import_files.c.kind).join(store.import_files,store.import_files.c.id==store.import_rows.c.file).where(store.import_rows.c.id==row['source'])).first()
                    if source and source.kind=='pdf':pdf=dict(file=source.file,name=source.name,page=source.row)
                declarations.append(dict(row,available=s.text_quantity(available),errors=errors,pdf=pdf))
            for row in s.list_rows(conn,s.expenses,org):
                d=row['data']
                if d.get('settlement'):expenses.append(dict(row,available=str(max(st.Decimal(0),st.paid(d)-st.number(d['excluded'])-st.used_cost(conn,row['id'],job_id))),issues=st.expense_issues(d),affected_jobs=intake.affected(conn,org,'cost',row['id'])))
            files=[dict(r) for r in conn.execute(select(s.evidence.c.id,s.evidence.c.name,s.evidence.c.sha,s.evidence.c.data).where(s.evidence.c.org==org)).mappings()]
            return dict(batches=batches,declarations=declarations,expenses=expenses,files=files,facilities=s.list_rows(conn,s.facilities,org))

    from epms import sorting_receipts as intake
    intake.install(app,db,gate,actor)
    from epms import support_budget as budget
    budget.install(app,db,gate,actor)
    from epms import monthly_report_api,monthly_report
    monthly_report_api.install(app,db,gate,actor)

    @app.get(prefix)
    def listing(org:int):
        with db.connect() as conn:
            gate(conn,org)
            return s.list_rows(conn,st.jobs,org)

    @app.post(prefix)
    def create(org:int,value:st.Job,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return st.job_save(conn,org,value,actor(request))

    @app.put(prefix+'/{key}')
    def update(org:int,key:int,value:st.Job,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return st.job_save(conn,org,value,actor(request),key)

    @app.post(prefix+'/{key}/preview')
    def preview(org:int,key:int,value:st.Job):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org);job=s.scoped(conn,st.jobs,org,key)
            if job['revision']!=value.revision:st.fail('작업이 변경되었습니다. 다시 조회하세요.',409)
            return st.calculate(conn,org,value.model_dump(exclude={'revision','request_key'}),key)

    @app.post(prefix+'/{key}/confirm')
    def confirm(org:int,key:int,value:st.Job,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return st.confirm(conn,org,key,value,actor(request))

    @app.post(prefix+'/{key}/release')
    def release(org:int,key:int,value:Action,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);job=s.scoped(conn,st.jobs,org,key)
            if job['revision']!=value.revision:st.fail('작업이 변경되었습니다.',409)
            monthly_report.ensure_unlocked(conn,org,key)
            conn.execute(st.links.update().where(st.links.c.job==key).values(active=0))
            conn.execute(st.cost_links.update().where(st.cost_links.c.job==key).values(active=0))
            conn.execute(budget.usage.update().where(budget.usage.c.org==org,budget.usage.c.job==key).values(active=0))
            conn.execute(st.jobs.update().where(st.jobs.c.id==key).values(state='DRAFT',revision=job['revision']+1))
            store.log(conn,org,actor(request),'settlement_release',key,value.reason,None,{'revision':job['revision']+1})
            return dict(revision=job['revision']+1)

    @app.get(prefix+'/{key}/history')
    def history(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,st.jobs,org,key)
            return [dict(id=r['id'],revision=r['revision'],data=r['data']) for r in conn.execute(st.snapshots.select().where(st.snapshots.c.job==key).order_by(st.snapshots.c.id.desc())).mappings()]

    @app.get(prefix+'/results/{key}/pdf')
    def pdf(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);row=s.scoped(conn,st.snapshots,org,key)
        return Response(row['pdf'],media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="settlement-{row["revision"]}.pdf"'})

    @app.post(prefix+'/sources')
    def source(org:int,value:st.Source,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return st.source_save(conn,org,value,actor(request))

    @app.put(prefix+'/sources/{key}')
    def source_edit(org:int,key:int,value:st.Source,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return st.source_save(conn,org,value,actor(request),key)

    @app.post(prefix+'/costs')
    def expense(org:int,value:st.Expense,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return st.expense_save(conn,org,value,actor(request))

    @app.put(prefix+'/costs/{key}')
    def expense_edit(org:int,key:int,value:st.Expense,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return st.expense_save(conn,org,value,actor(request),key)

    @app.post(prefix+'/documents/upload')
    async def upload(org:int,name:str,kind:str,request:Request):
        if not name.lower().endswith('.pdf') or len(name)>240 or any(c in name for c in '/\\:\r\n'):st.fail('PDF 파일명을 확인하세요.')
        with db.connect() as conn:gate(conn,org)
        content=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>20_000_000:st.fail('PDF는 20MB 이하여야 합니다.',413)
            content.append(chunk)
        data=b''.join(content)
        try:
            with ocr.PDF_LOCK,ocr.open_pdf(data) as doc:
                pages=len(doc)
                if not 1<=pages<=150:st.fail('PDF는 1~150쪽이어야 합니다.')
        except ValueError as e:st.fail(str(e))
        digest=sha256(data).hexdigest()
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            row=conn.execute(s.evidence.select().where(s.evidence.c.org==org,s.evidence.c.sha==digest)).mappings().first()
            if row:return dict(id=row['id'],duplicate=True,name=row['name'],pages=pages)
            key=conn.execute(s.evidence.insert().values(org=org,sha=digest,name=name,content=data,data=dict(kind=kind,pages=pages,created=store.now(),settlement=True))).inserted_primary_key[0]
            store.log(conn,org,actor(request),'settlement_pdf',key,'PDF 증빙 등록',None,dict(name=name,sha=digest))
            return dict(id=key,duplicate=False,name=name,pages=pages)

    @app.get(prefix+'/documents/{key}/page/{page}')
    def image(org:int,key:int,page:int):
        with db.connect() as conn:gate(conn,org);row=s.scoped(conn,s.evidence,org,key)
        try:return Response(ocr.page_image(row['content'],page),media_type='image/png')
        except ValueError as e:st.fail(str(e))

    @app.get(prefix+'/documents/{key}/original')
    def original(org:int,key:int):
        with db.connect() as conn:gate(conn,org);row=s.scoped(conn,s.evidence,org,key)
        return Response(row['content'],media_type='application/pdf')

    @app.post(prefix+'/documents/{key}/ocr/{page}')
    def extract(org:int,key:int,page:int,request:Request):
        with db.connect() as conn:gate(conn,org);row=s.scoped(conn,s.evidence,org,key)
        try:
            with ocr.PDF_LOCK,ocr.open_pdf(row['content']) as doc:
                if not 1<=page<=len(doc):st.fail('PDF 페이지를 확인하세요.')
                with closing(doc[page-1]) as p,closing(p.get_textpage()) as layer:
                    text=layer.get_text_range()[:50000];method='PDF 텍스트';warning=''
                    if not text.strip():
                        image=ocr.render(p)
                        payload,warning=ocr.recognize(image)
                        text='\n'.join(line.get('text','') for line in (payload or {}).get('lines',[]));method='Windows 로컬 OCR'
            candidates=[]
            patterns={'export_quantity':'수출(?:용)?(?:\\s*선별)?(?:\\s*물량|량|중량)', 'domestic':'내수(?:물량|량|중량)?','rejected':'탈락(?:물량|량)?',
                'total':'(?:총|전체)(?:\\s*취급|\\s*투입)?(?:물량|량|중량)','gross':'(?:지급\\s*총액|급여\\s*합계|총\\s*급여)', 'start':'(?:작업일|선별일|대상기간)'}
            for field,label in patterns.items():
                for match in re.finditer('(?:'+label+r')[ \t]*[:：][ \t]*([^\r\n]{1,100})',text):candidates.append(dict(field=field,value=match[1].strip(),page=page))
            value=dict(page=page,text=text,method=method,warning=warning or ('' if text.strip() else '추출 실패: 원문을 보고 수동 입력하세요.'),candidates=candidates,sha=row['sha'],confirmed=False)
        except ValueError as e:st.fail(str(e))
        with db.begin() as conn:
            conn.execute(s.records.insert().values(org=org,kind='SETTLEMENT_OCR',revision=1,data=dict(value,evidence_id=key,time=store.now())))
        return value

    @app.get(prefix+'/documents/{key}/ocr')
    def ocr_history(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,s.evidence,org,key)
            return [r['data'] for r in s.list_rows(conn,s.records,org) if r['kind']=='SETTLEMENT_OCR' and r['data']['evidence_id']==key]
