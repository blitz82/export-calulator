"""Current shared facilities and read-only legacy archives; no old workflow writers."""
import os
from typing import Literal
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field
from epms import sorting as s,sorting_facility_review as facilities,sorting_archive as archive

class Facility(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name:str=Field(min_length=1,max_length=100)
    product:str=Field(min_length=1,max_length=100)
    member_number:str=Field(min_length=10,max_length=12)
    designation:str=Field(min_length=1,max_length=100)
    operator:str=Field(default='',max_length=100)
    address:str=Field(default='',max_length=200)
    valid_from:str=Field(max_length=10)
    valid_to:str|None=Field(default=None,max_length=10)
    evidence_id:int

class Revision(BaseModel):
    revision:int=Field(ge=1)
    reason:str=Field(min_length=1,max_length=500)
    data:Facility

class Release(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    revision:int=Field(ge=1)
    reason:str=Field(min_length=1,max_length=500)

def install(app,db,organization,actor):
    app.state.sorting_enabled=os.environ.get('EPMS_SORTING_ENABLED')=='1'
    @app.exception_handler(s.SortingError)
    async def sorting_error(_,exc):
        return JSONResponse(status_code=exc.status,content=dict(detail=exc.message,issues=[s.problem(exc.code,exc.field,exc.message)]))
    def gate(conn,org):
        if not app.state.sorting_enabled:raise HTTPException(404,'공동선별비 기능이 꺼져 있습니다.')
        organization(conn,org)
    def xlsx(content,name):
        return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{name}.xlsx"'})
    @app.get('/api/sorting-capabilities')
    def capabilities():return dict(feature_enabled=app.state.sorting_enabled,operation_mode='SETTLEMENT_REFERENCE',operational_approval=False,legacy_read_only=True)
    prefix='/api/org/{org}/sorting'
    @app.get(prefix+'/status')
    def status(org:int):
        with db.connect() as conn:gate(conn,org)
        return dict(feature_enabled=True,operation_mode='SETTLEMENT_REFERENCE',legacy_read_only=True)
    @app.get(prefix+'/facilities')
    def listing(org:int):
        with db.connect() as conn:gate(conn,org);return s.list_rows(conn,s.facilities,org)
    @app.post(prefix+'/facilities')
    def create(org:int,data:Facility,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return dict(id=s.add_facility(conn,org,data.model_dump(),actor(request)))
    @app.post(prefix+'/facilities/{key}/revise')
    def revise(org:int,key:int,data:Revision,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return facilities.revise(conn,org,key,data.model_dump(),actor(request))
    @app.get(prefix+'/facilities/{key}/history')
    def history(org:int,key:int):
        with db.connect() as conn:gate(conn,org);return facilities.history(conn,org,key)
    @app.get(prefix+'/capacity')
    def capacity(org:int,declaration_id:int,declaration_revision:int,batch_id:int,dispatch_id:int|None=None):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org)
            return s.capacity(conn,org,declaration_id,declaration_revision,batch_id,dispatch_id)
    @app.get(prefix+'/archive')
    def archives(org:int):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org)
            return [archive.inspect(conn,org,r['id']) for r in s.list_rows(conn,s.claims,org)]
    @app.get(prefix+'/archive/{key}')
    def archive_detail(org:int,key:int):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org);return archive.detail(conn,org,key)
    @app.post(prefix+'/archive/{key}/release')
    def release(org:int,key:int,data:Release,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(conn,org)
            return archive.release(conn,org,key,data.revision,data.reason,actor(request))
    @app.get(prefix+'/claims')
    def old_claims(org:int):
        with db.connect() as conn:gate(conn,org);return s.list_rows(conn,s.claims,org)
    @app.get(prefix+'/claims/{key}')
    def old_claim(org:int,key:int):
        with db.connect() as conn:gate(conn,org);return s.review(conn,org,key)
    @app.get(prefix+'/claims/{key}/report')
    def old_report(org:int,key:int,official:bool=False):
        from epms import sorting_books
        with db.connect() as conn:
            gate(conn,org)
            if official:s.operation_guard()
            result=s.review(conn,org,key)
        return xlsx(sorting_books.report(result),'sorting-review')
    @app.get(prefix+'/claims/{key}/print')
    def old_print(org:int,key:int,official:bool=False):
        from epms import sorting_print
        with db.connect() as conn:
            gate(conn,org)
            if official:s.operation_guard()
            result=s.review(conn,org,key)
        return Response(sorting_print.render(result),media_type='text/html; charset=utf-8')
    @app.get(prefix+'/paper-cases/{key}')
    def paper_view(org:int,key:int):
        from epms import sorting_paper
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org);return sorting_paper.view(conn,org,key)
    @app.get(prefix+'/paper-cases/{key}/report')
    def paper_report(org:int,key:int,kind:Literal['SUBMISSION','INSTRUCTION','RESULT']='SUBMISSION',official:bool=False):
        from epms import sorting_paper
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(conn,org)
            if official:s.operation_guard()
            result=sorting_paper.report_body(conn,org,key,kind,None)
        return xlsx(sorting_paper.workbook(result,kind),'legacy-paper-result')
    @app.get(prefix+'/beneficiary-statements/{key}/report')
    def beneficiary_report(org:int,key:int,official:bool=False):
        from epms import sorting_beneficiaries
        with db.connect() as conn:
            gate(conn,org)
            if official:s.operation_guard()
            result=sorting_beneficiaries.statement(conn,org,key,None)
        return xlsx(sorting_beneficiaries.workbook(result),'legacy-beneficiary')
    @app.get(prefix+'/payment-preparations/{key}/report')
    def preparation_report(org:int,key:int,official:bool=False):
        from epms import sorting_payment_preparation
        with db.connect() as conn:
            gate(conn,org)
            if official:s.operation_guard()
            result=sorting_payment_preparation.workbook(conn,org,key,None)
        return xlsx(result,'legacy-preparation')
    @app.api_route(prefix+'/{retired:path}',methods=['POST','PUT','PATCH','DELETE'])
    def retired(org:int,retired:str):
        with db.connect() as conn:gate(conn,org)
        raise HTTPException(410,'종전 공동선별 검토 작업은 종료되었습니다. 공동선별비 정산을 이용하세요. 과거 예약은 보관함에서 확인·해제할 수 있습니다.')
