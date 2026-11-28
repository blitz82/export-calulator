from epms.downloads import attachment
from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import select
from epms import monthly_report as m,sorting as s,store,settlement as st

def install(app,db,gate,actor):
    from epms import workbench
    workbench.install(app,db,gate,actor)
    path='/api/org/{org}/monthly-reports'
    @app.get(path+'/catalog')
    def catalog(org:int):
        with db.connect() as conn:gate(conn,org);return m.catalog(conn,org)
    @app.get(path)
    def listing(org:int):
        with db.connect() as conn:
            gate(conn,org)
            return [dict(r,package=conn.execute(select(m.packages.c.id).where(m.packages.c.report==r['id'])).scalar()) for r in s.list_rows(conn,m.reports,org)]
    @app.post(path)
    def create(org:int,value:m.Draft,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return m.save(conn,org,value,actor(request))
    @app.put(path+'/{key}')
    def save(org:int,key:int,value:m.Draft,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return m.save(conn,org,value,actor(request),key)
    @app.get(path+'/{key}/preview')
    def preview(org:int,key:int):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org);return m.preview(conn,org,key)
    @app.post(path+'/{key}/freeze')
    def freeze(org:int,key:int,value:m.Action,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);return m.freeze(conn,org,key,value.revision,actor(request))
    @app.post(path+'/{key}/unlock')
    def unlock(org:int,key:int,value:m.Action,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);r=s.scoped(conn,m.reports,org,key)
            if r['revision']!=value.revision or r['state'] not in ('FROZEN','SUBMITTED'):st.fail('보고서 상태·버전을 다시 확인하세요.',409)
            package=conn.execute(select(m.packages.c.id).where(m.packages.c.report==key)).scalar_one()
            conn.execute(m.claims.update().where(m.claims.c.package==package).values(active=0))
            conn.execute(m.transfers.update().where(m.transfers.c.package==package).values(active=0))
            conn.execute(m.reports.update().where(m.reports.c.id==key).values(state='UNLOCKED',revision=r['revision']+1))
            store.log(conn,org,actor(request),'monthly_report',key,'제출본 잠금 해제',None,dict(reason=value.reason,package=package))
            return dict(ok=True)
    @app.post(path+'/{key}/submitted')
    def submitted(org:int,key:int,value:m.Submitted,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org);r=s.scoped(conn,m.reports,org,key)
            if r['revision']!=value.revision or r['state'] not in ('FROZEN','SUBMITTED'):st.fail('보존된 최신 제출본에서 제출 이력을 기록하세요.',409)
            if not st.rules.day(value.date):st.fail('실제 제출일을 확인하세요.')
            st.refs(conn,org,value.evidence)
            conn.execute(m.reports.update().where(m.reports.c.id==key).values(state='SUBMITTED',revision=r['revision']+1))
            store.log(conn,org,actor(request),'monthly_report',key,'실제 제출 기록',None,value.model_dump())
            return dict(ok=True)
    @app.get(path+'/{key}/history')
    def history(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,m.reports,org,key)
            return [dict(r) for r in conn.execute(store.audit.select().where(store.audit.c.org==org,store.audit.c.entity.in_(['monthly_report','monthly_package']),store.audit.c.target==key).order_by(store.audit.c.id.desc())).mappings()]
    @app.get(path+'/{key}/package')
    def package(org:int,key:int):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,m.reports,org,key)
            p=conn.execute(m.packages.select().where(m.packages.c.report==key)).mappings().first()
            if not p:st.fail('아직 제출본이 생성되지 않았습니다.',404)
            return dict(id=p['id'],sha=p['sha'],data=p['data'])
    @app.get(path+'/{key}/pdf')
    def pdf(org:int,key:int,originals:bool=False):
        with db.connect() as conn:
            gate(conn,org);s.scoped(conn,m.reports,org,key)
            p=conn.execute(m.packages.select().where(m.packages.c.report==key)).mappings().first()
            if not p:st.fail('제출본을 먼저 생성하세요.',404)
            edition=len(m.family(conn,org,key))
        ext='zip' if originals else 'pdf'
        return Response(p['archive'] if originals else p['pdf'],media_type='application/zip' if originals else 'application/pdf',headers={'Content-Disposition':attachment(f"{p['data']['data']['year']}-{p['data']['data']['month']:02d}_{p['data'].get('organization','통합조직')}_공동선별비_제출본_{edition}차_보고{key}.{ext}",f'monthly-report-{key}.{ext}')})

