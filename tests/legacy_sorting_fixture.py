"""Test-only writer for historical regression fixtures. Never imported by the application."""
import hashlib
import json
import os
from typing import Literal
from zipfile import BadZipFile
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from epms import sorting as s, sorting_books as books, store
from epms import sorting_finance as finance
from epms import sorting_records
from epms import sorting_production as production, sorting_documents
from epms import sorting_evidence_review as linked
from epms import sorting_reconciliation as reconciliation,sorting_print
from epms import sorting_pdf_review as pdf_review
from epms import sorting_dashboard
from epms import sorting_intake_import as intake_import
from epms import sorting_cost_review
from epms import sorting_policy_review,sorting_snapshots,sorting_group_review
from epms import sorting_facility_review
from epms import sorting_contract_review
from epms import sorting_beneficiaries
from epms import sorting_payment_preparation as payment_preparation
from epms import sorting_readiness
from epms import sorting_paper as paper


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PaperSubmission(Strict):
    preparation_id:int = Field(gt=0)
    preparation_revision:int = Field(ge=1)
    document_number:str = Field(min_length=1,max_length=100)
    submitted_date:str = Field(max_length=10)
    policy_basis:str = Field(min_length=1,max_length=2000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PaperDecisionMember(Strict):
    member_id:int = Field(gt=0)
    result:Literal['APPROVED','HOLD','REJECTED']
    amount:str|None = Field(default=None,max_length=30)
    difference_reason:str = Field(default='',max_length=500)


class PaperDecision(Strict):
    previous_id:int|None = Field(default=None,gt=0)
    document_number:str = Field(min_length=1,max_length=100)
    decision_date:str = Field(max_length=10)
    evidence_id:int = Field(gt=0)
    decision_basis:str = Field(min_length=1,max_length=2000)
    change_note:str = Field(default='',max_length=1000)
    entry_correction:bool = False
    members:list[PaperDecisionMember] = Field(min_length=1,max_length=1000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PaperPaymentLine(Strict):
    case_id:int = Field(gt=0)
    decision_id:int = Field(gt=0)
    member_id:int = Field(gt=0)
    amount:str = Field(max_length=30)


class PaperPayment(Strict):
    account_reference:str = Field(min_length=1,max_length=100)
    transaction_id:str = Field(min_length=1,max_length=100)
    paid_date:str = Field(max_length=10)
    bank_amount:str = Field(max_length=30)
    status:Literal['SUCCESS','FAILED','CANCELLED']
    evidence_id:int = Field(gt=0)
    retry_of:int|None = Field(default=None,gt=0)
    replaces_payment_id:int|None = Field(default=None,gt=0)
    source_change_note:str = Field(default='',max_length=1000)
    lines:list[PaperPaymentLine] = Field(min_length=1,max_length=1000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PaperRefundLine(Strict):
    case_id:int = Field(gt=0)
    member_id:int = Field(gt=0)
    amount:str = Field(max_length=30)


class PaperAdjustment(Strict):
    action:Literal['VOID','REFUND','VOID_REFUND']
    date:str = Field(max_length=10)
    evidence_id:int = Field(gt=0)
    account_reference:str = Field(default='',max_length=100)
    transaction_id:str = Field(default='',max_length=100)
    bank_amount:str|None = Field(default=None,max_length=30)
    target_adjustment_id:int|None = Field(default=None,gt=0)
    replaces_adjustment_id:int|None = Field(default=None,gt=0)
    lines:list[PaperRefundLine] = Field(default_factory=list,max_length=1000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PaperClose(Strict):
    date:str = Field(max_length=10)
    evidence_id:int = Field(gt=0)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class BeneficiaryCost(Strict):
    expense_id:int = Field(gt=0)
    amount:str = Field(max_length=30)


class BeneficiaryRow(Strict):
    line_id:int = Field(gt=0)
    member_id:int = Field(gt=0)
    quantity:str = Field(max_length=30)
    evidence_id:int = Field(gt=0)
    source_reference:str = Field(min_length=1,max_length=200)
    basis:str = Field(min_length=1,max_length=1000)
    costs:list[BeneficiaryCost] = Field(default_factory=list,max_length=100)
    reference_amount:str|None = Field(default=None,max_length=30)


class BeneficiaryInput(Strict):
    revision:int = Field(ge=1)
    rows:list[BeneficiaryRow] = Field(max_length=1000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PreparationCreate(Strict):
    claim_revision:int = Field(ge=1)
    statement_id:int = Field(gt=0)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PreparationMember(Strict):
    member_id:int = Field(gt=0)
    prepared_amount:str|None = Field(default=None,max_length=30)
    planned_date:str|None = Field(default=None,max_length=10)
    documents_reviewed:bool = False
    basis:str = Field(default='',max_length=500)


class PreparationRevision(Strict):
    revision:int = Field(ge=1)
    state:Literal['DRAFT','HOLD','INPUT_REVIEWED','CANCELLED']
    members:list[PreparationMember] = Field(min_length=1,max_length=1000)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class PolicyDraft(Strict):
    policy_id:str = Field(pattern='^P-(0[1-9]|1[0-5])$')
    year:int = Field(ge=2027,le=2100)
    product:str = Field(min_length=1,max_length=100)
    start:str = Field(max_length=10)
    end:str = Field(max_length=10)
    state:Literal['DRAFT','RETIRED']='DRAFT'
    choice:str = Field(min_length=1,max_length=1000)
    basis:str = Field(min_length=1,max_length=1000)
    decision_reference:str = Field(default='',max_length=200)
    reason:str = Field(min_length=1,max_length=500)
    evidence_id:int = Field(gt=0)
    previous_id:int|None = None
    previous_revision:int|None = None
    request_key:str = Field(min_length=8,max_length=100)


class SnapshotInput(Strict):
    revision:int = Field(ge=1)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class ContractLink(Strict):
    revision:int = Field(ge=1)
    contract_id:int|None = Field(default=None,gt=0)
    reason:str = Field(min_length=1,max_length=500)
    request_key:str = Field(min_length=8,max_length=100)


class ChangeInput(Strict):
    current_hash:str = Field(pattern='^[0-9a-f]{64}$')
    reason:str = Field(min_length=1,max_length=500)


class RevisionInput(Strict):
    revision:int = Field(ge=1)
    reason:str = Field(min_length=1,max_length=500)
    data:dict = Field(max_length=35)


class GroupClaim(Strict):
    id:int = Field(gt=0)
    revision:int = Field(ge=1)


class GroupPortion(Strict):
    portion_id:str = Field(min_length=1,max_length=100)
    amount:str = Field(max_length=30)


class GroupInput(Strict):
    claims:list[GroupClaim] = Field(min_length=1,max_length=100)
    unit_price:str = Field(max_length=30)
    available_budget:str|None = Field(default=None,max_length=30)
    commitments:list[GroupPortion]|None = Field(default=None,max_length=1000)


class IntakeMapping(Strict):
    evidence_id:int = Field(gt=0)
    encoding:Literal['utf-8-sig','cp949']='utf-8-sig'
    facility_id:int = Field(gt=0)
    system:str = Field(min_length=1,max_length=30)
    mapping:dict[str,int] = Field(min_length=6,max_length=10)


class IntakeCommit(IntakeMapping):
    preview_hash:str = Field(pattern='^[0-9a-f]{64}$')
    request_key:str = Field(min_length=8,max_length=100)
    rows:list[int] = Field(min_length=1,max_length=1000)


class PDFProcess(Strict):
    evidence_id:int = Field(gt=0)
    start:int = Field(ge=1,le=150)
    count:int = Field(default=1,ge=1,le=5)
    force:bool = False


class PDFReview(Strict):
    evidence_id:int = Field(gt=0)
    page:int = Field(ge=1,le=150)
    extraction_hash:str = Field(pattern='^[0-9a-f]{64}$')
    state:Literal['LOCALLY_REVIEWED','NEEDS_SUPPLEMENT']
    reason:str = Field(min_length=1,max_length=500)


class PDFCompare(Strict):
    pdf_id:int = Field(gt=0)
    excel_id:int = Field(gt=0)
    sheet:str = Field(min_length=1,max_length=100)


class Facility(Strict):
    name: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    member_number: str = Field(min_length=10, max_length=12)
    designation: str = Field(min_length=1, max_length=100)
    operator: str = Field(default='', max_length=100)
    address: str = Field(default='', max_length=200)
    valid_from: str = Field(max_length=10)
    valid_to: str = Field(max_length=10)
    evidence_id: int


class FacilityRevision(Strict):
    revision:int = Field(ge=1)
    reason:str = Field(min_length=1,max_length=500)
    data:Facility


class Batch(Strict):
    facility_id: int
    external_id: str = Field(min_length=1, max_length=100)
    work_date: str = Field(max_length=10)
    dispatch_date: str | None = Field(default=None, max_length=10)
    product: str = Field(min_length=1, max_length=100)
    quantity: str = Field(max_length=30)
    unit: str = Field(max_length=10)
    evidence_id: int
    note: str = Field(default='', max_length=500)


class Allocation(Strict):
    declaration_id: int
    declaration_revision: int
    batch_id: int
    batch_revision: int
    quantity: str = Field(max_length=30)
    dispatch_id: int | None = None


class Claim(Strict):
    request_key: str = Field(min_length=8, max_length=100)
    year: int = Field(ge=2026, le=2100)
    product: str = Field(min_length=1, max_length=100)
    profile: str = Field(max_length=80)
    lines: list[Allocation] = Field(min_length=1, max_length=1000)


class Transition(Strict):
    revision: int
    action: str = Field(max_length=30)
    reason: str = Field(min_length=1, max_length=500)


class EvidenceReview(Strict):
    revision:int = Field(ge=1)
    request_key:str = Field(min_length=8,max_length=100)
    action:Literal['RECORD','REVOKE']='RECORD'
    actual_shipped:str | None = Field(default=None,max_length=10)
    fulfillment_evidence_id:int | None = Field(default=None,gt=0)
    fulfillment_kind:Literal['CUSTOMS_FULFILLMENT','LOADING_CONFIRMATION','OTHER_OBJECTIVE','TRANSPORT_AUXILIARY'] | None = None
    certificate_id:int | None = Field(default=None,gt=0)
    reason:str = Field(min_length=1,max_length=500)


class Calculation(Strict):
    quantity: str = Field(max_length=30)
    unit_price: str = Field(max_length=30)
    eligible_cost: str | None = Field(default=None, max_length=30)


class BenefitPortion(Strict):
    portion_id:str = Field(min_length=1,max_length=100)
    quantity:str = Field(max_length=30)


class FormCalculation(Strict):
    total:str = Field(max_length=30)
    unit_price:str = Field(max_length=30)
    benefits:dict[str,list[BenefitPortion] | None] | None = None
    form_version:str = Field(default='2026-09-01',max_length=50)


class ImportSelection(Strict):
    evidence_id: int
    sheet: str = Field(min_length=1,max_length=100)
    facility_id: int
    product: str = Field(min_length=1,max_length=100)
    rows: list[int] = Field(min_length=1,max_length=1000)


class Reconciliation(Strict):
    left_id:int = Field(gt=0)
    left_sheet:str = Field(min_length=1,max_length=100)
    right_id:int = Field(gt=0)
    right_sheet:str = Field(min_length=1,max_length=100)


class Expense(Strict):
    facility_id: int
    external_id: str = Field(min_length=1,max_length=100)
    month: str = Field(max_length=7)
    category: str = Field(max_length=40)
    amount: str = Field(max_length=30)
    separated: bool
    evidence_id: int
    separation_evidence_id: int | None = None
    pseudonym: str = Field(min_length=1,max_length=80)


class PaymentHistory(Strict):
    expense_id: int
    transaction_id: str = Field(min_length=1,max_length=100)
    amount: str = Field(max_length=30)
    status: str = Field(max_length=30)
    date: str = Field(max_length=10)
    evidence_id: int
    retry_of: int | None = None


class CostAllocation(Strict):
    expense_id:int
    amount:str = Field(max_length=30)


class ReviewRecord(Strict):
    kind:str = Field(max_length=40)
    data:dict = Field(max_length=40)
    previous_id:int | None = None


class Intake(Strict):
    facility_id:int
    external_id:str = Field(min_length=1,max_length=100)
    product:str = Field(min_length=1,max_length=100)
    producer_id:str = Field(min_length=1,max_length=100)
    date:str = Field(max_length=10)
    quantity:str = Field(max_length=30)
    unit:str = Field(max_length=10)
    evidence_id:int


class ParentLot(Strict):
    kind:str = Field(max_length=10)
    id:int
    quantity:str = Field(max_length=30)


class Transform(Strict):
    facility_id:int
    external_id:str = Field(min_length=1,max_length=100)
    product:str = Field(min_length=1,max_length=100)
    date:str = Field(max_length=10)
    evidence_id:int
    inputs:list[ParentLot] = Field(min_length=1,max_length=1000)
    export_quantity:str = Field(max_length=30)
    domestic_quantity:str = Field(max_length=30)
    reject_quantity:str = Field(max_length=30)
    loss_quantity:str = Field(max_length=30)
    inventory_quantity:str = Field(max_length=30)


class Dispatch(Strict):
    batch_id:int
    external_id:str = Field(min_length=1,max_length=100)
    exporter_id:str = Field(min_length=1,max_length=100)
    date:str = Field(max_length=10)
    quantity:str = Field(max_length=30)
    evidence_id:int


def install(app, db, organization, actor):
    enabled = os.environ.get('EPMS_SORTING_ENABLED') == '1'
    app.state.sorting_enabled = enabled

    @app.exception_handler(s.SortingError)
    async def sorting_error(_, exc):
        return JSONResponse(status_code=exc.status, content=dict(detail=exc.message, issues=[s.problem(exc.code, exc.field, exc.message)]))

    @app.get('/api/sorting-capabilities')
    def capabilities():
        return dict(feature_enabled=enabled, operation_mode='REVIEW_ONLY', operational_approval=False,external_paper_records=True)

    def gate(org, conn):
        if not enabled:
            raise HTTPException(404, '공동선별비 검토 기능이 꺼져 있습니다.')
        organization(conn, org)

    prefix = '/api/org/{org}/sorting'
    media = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    @app.get(prefix + '/claims/{entity}/paper-cases')
    def paper_cases(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            return paper.listing(conn,org,entity)

    @app.post(prefix + '/paper-cases')
    def paper_submit(org:int,data:PaperSubmission,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return paper.submit(conn,org,data.model_dump(),actor(request))

    @app.get(prefix + '/paper-cases')
    def paper_all_cases(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return [dict(id=r['id'],claim_id=r['data']['claim_id'],document_number=r['data']['document_number'],closed=paper.closed(conn,org,r['id'])) for r in paper.records(conn,org,paper.CASE)]

    @app.get(prefix + '/paper-cases/{entity}')
    def paper_view(org:int,entity:int):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            return paper.view(conn,org,entity)

    @app.post(prefix + '/paper-cases/{entity}/decisions')
    def paper_decision(org:int,entity:int,data:PaperDecision,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return paper.decide(conn,org,entity,data.model_dump(),actor(request))

    @app.post(prefix + '/paper-cases/{entity}/close')
    def paper_close(org:int,entity:int,data:PaperClose,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return paper.close(conn,org,entity,data.model_dump(),actor(request))

    @app.post(prefix + '/paper-bank-results')
    def paper_bank_result(org:int,data:PaperPayment,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return paper.payment(conn,org,data.model_dump(),actor(request))

    @app.post(prefix + '/paper-bank-results/{entity}/adjustments')
    def paper_bank_adjustment(org:int,entity:int,data:PaperAdjustment,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return paper.adjustment(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/paper-cases/{entity}/report')
    def paper_report(org:int,entity:int,kind:Literal['SUBMISSION','INSTRUCTION','RESULT']='SUBMISSION',member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            if official:s.operation_guard()
            state=paper.report_body(conn,org,entity,kind,member_id)
        return Response(paper.workbook(state,kind),media_type=media,headers={'Content-Disposition':"attachment; filename*=UTF-8''paper-procedure.xlsx"})

    @app.get(prefix + '/paper-cases/{entity}/print')
    def paper_print(org:int,entity:int,kind:Literal['SUBMISSION','INSTRUCTION','RESULT']='SUBMISSION',member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            if official:s.operation_guard()
            state=paper.report_body(conn,org,entity,kind,member_id)
        return Response(paper.printable(state,kind),media_type='text/html; charset=utf-8')

    @app.get(prefix + '/claims/{entity}/readiness')
    def readiness(org:int,entity:int,statement_id:int|None=None,preparation_id:int|None=None):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            return sorting_readiness.review(conn,org,entity,statement_id,preparation_id)

    @app.get(prefix + '/claims/{entity}/payment-preparations')
    def preparation_list(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            return payment_preparation.listing(conn,org,entity)

    @app.post(prefix + '/claims/{entity}/payment-preparations')
    def preparation_create(org:int,entity:int,data:PreparationCreate,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return payment_preparation.create(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/payment-preparations/{entity}')
    def preparation_view(org:int,entity:int,member_id:int|None=None):
        with db.connect() as conn:
            gate(org,conn)
            return payment_preparation.view(conn,org,entity,member_id)

    @app.post(prefix + '/payment-preparations/{entity}/revise')
    def preparation_revise(org:int,entity:int,data:PreparationRevision,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return payment_preparation.revise(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/payment-preparations/{entity}/report')
    def preparation_report(org:int,entity:int,member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:s.operation_guard()
            content=payment_preparation.workbook(conn,org,entity,member_id)
        return Response(content,media_type=media,headers={'Content-Disposition':"attachment; filename*=UTF-8''payment-preparation-review.xlsx"})

    @app.get(prefix + '/payment-preparations/{entity}/print')
    def preparation_print(org:int,entity:int,member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:s.operation_guard()
            state=payment_preparation.view(conn,org,entity,member_id)
        return Response(payment_preparation.printable(state),media_type='text/html; charset=utf-8')

    @app.post(prefix + '/payment-preparations/{entity}/{action}')
    def preparation_operational(org:int,entity:int,action:str):
        with db.connect() as conn:
            gate(org,conn);payment_preparation.history(conn,org,entity);s.operation_guard()

    @app.get(prefix + '/claims/{entity}/beneficiaries')
    def beneficiary_review(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            return sorting_beneficiaries.view(conn,org,entity)

    @app.post(prefix + '/claims/{entity}/beneficiaries')
    def beneficiary_save(org:int,entity:int,data:BeneficiaryInput,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_beneficiaries.save(conn,org,entity,data.model_dump(),actor(request))

    @app.post(prefix + '/claims/{entity}/beneficiary-statements')
    def beneficiary_statement_save(org:int,entity:int,data:SnapshotInput,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_beneficiaries.freeze(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/beneficiary-statements/{entity}')
    def beneficiary_statement(org:int,entity:int,member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:s.operation_guard()
            return sorting_beneficiaries.statement(conn,org,entity,member_id)

    @app.get(prefix + '/beneficiary-statements/{entity}/report')
    def beneficiary_statement_report(org:int,entity:int,member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:s.operation_guard()
            body=sorting_beneficiaries.statement(conn,org,entity,member_id)
        return Response(sorting_beneficiaries.workbook(body),media_type=media,headers={'Content-Disposition':"attachment; filename*=UTF-8''beneficiary-review.xlsx"})

    @app.get(prefix + '/beneficiary-statements/{entity}/print')
    def beneficiary_statement_print(org:int,entity:int,member_id:int|None=None,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:s.operation_guard()
            body=sorting_beneficiaries.statement(conn,org,entity,member_id)
        return Response(sorting_beneficiaries.printable(body),media_type='text/html; charset=utf-8')

    @app.get(prefix + '/claims/{entity}/lines/{line}/contract-review')
    def contract_review(org:int,entity:int,line:int):
        with db.connect() as conn:
            gate(org,conn)
            return sorting_contract_review.assess(conn,org,entity,line)

    @app.post(prefix + '/claims/{entity}/lines/{line}/contract-review')
    def contract_link(org:int,entity:int,line:int,data:ContractLink,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_contract_review.record(conn,org,entity,line,data.model_dump(),actor(request))

    @app.post(prefix + '/facilities/{entity}/revise')
    def revise_facility(org:int,entity:int,data:FacilityRevision,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_facility_review.revise(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/facilities/{entity}/history')
    def facility_history(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            return sorting_facility_review.history(conn,org,entity)

    @app.get(prefix + '/policy-drafts')
    def policy_drafts(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return sorting_policy_review.listing(conn,org)

    @app.post(prefix + '/policy-drafts')
    def policy_draft(org:int,data:PolicyDraft,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_policy_review.register(conn,org,data.model_dump(),actor(request))

    @app.post(prefix + '/records/{entity}/revise')
    def revise_record(org:int,entity:int,data:RevisionInput,request:Request):
        if not data.reason.strip():
            raise HTTPException(422,'개정 사유가 필요합니다.')
        if len(json.dumps(data.data))>20_000:
            raise HTTPException(413,'검토 기록 크기를 초과했습니다.')
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            previous=s.scoped(conn,s.records,org,entity)
            if previous['revision']!=data.revision:
                raise HTTPException(409,'기록 개정이 변경되었습니다.')
            try:
                target=sorting_records.append(conn,org,previous['kind'],dict(data.data,revision_reason=data.reason),actor(request),entity)
            except (KeyError,TypeError) as exc:
                raise HTTPException(422,'개정 필수값을 확인하세요.') from exc
            return dict(id=target)

    @app.get(prefix + '/claims/{entity}/snapshots')
    def snapshots(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            return sorting_snapshots.history(conn,org,entity)

    @app.post(prefix + '/claims/{entity}/snapshots')
    def snapshot_save(org:int,entity:int,data:SnapshotInput,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_snapshots.save(conn,org,entity,data.model_dump(),actor(request))

    @app.get(prefix + '/snapshots/{entity}/compare')
    def snapshot_compare(org:int,entity:int):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            return sorting_snapshots.compare(conn,org,entity)

    @app.get(prefix + '/snapshots/{entity}/report')
    def snapshot_report(org:int,entity:int):
        with db.connect() as conn:
            gate(org,conn)
            row=sorting_snapshots.get(conn,org,entity)
        return Response(json.dumps(row,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':"attachment; filename*=UTF-8''review-snapshot.json"})

    @app.post(prefix + '/snapshots/{entity}/change-candidate')
    def change_candidate(org:int,entity:int,data:ChangeInput,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE');gate(org,conn)
            return sorting_snapshots.candidate(conn,org,entity,data.model_dump(),actor(request))

    @app.post(prefix + '/group-review')
    def group_review(org:int,data:GroupInput):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN');gate(org,conn)
            return sorting_group_review.calculate(conn,org,data.model_dump())

    def pdf_call(function,*args):
        try:
            return function(*args)
        except s.SortingError:
            raise
        except (ValueError, OSError, BadZipFile, sorting_documents.pdfium.PdfiumError) as exc:
            raise HTTPException(422,'PDF·Excel 형식과 처리 범위를 확인하세요.') from exc

    @app.get(prefix + '/pdf-status')
    def pdf_status(org:int,evidence_id:int):
        with db.connect() as conn:
            gate(org,conn)
            return pdf_call(pdf_review.status,conn,org,evidence_id)

    @app.post(prefix + '/pdf-process')
    def pdf_process(org:int,data:PDFProcess):
        with db.connect() as conn:
            gate(org,conn)
        return pdf_call(pdf_review.process,db,org,data.evidence_id,data.start,data.count,data.force)

    @app.get(prefix + '/pdf-image')
    def pdf_image(org:int,evidence_id:int,page:int):
        with db.connect() as conn:
            gate(org,conn)
            info=pdf_call(pdf_review.status,conn,org,evidence_id)
            if not 1<=page<=info['pages']:
                raise HTTPException(422,'유효한 쪽 번호가 필요합니다.')
            file=pdf_review.source(conn,org,evidence_id)
        return Response(pdf_call(sorting_documents.ocr.page_image,file['content'],page),media_type='image/png',headers={'Cache-Control':'no-store'})

    @app.post(prefix + '/pdf-page-review')
    def pdf_page_review(org:int,data:PDFReview,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            if not data.reason.strip():
                raise HTTPException(422,'검토 근거가 필요합니다.')
            return pdf_review.review_page(conn,org,data.evidence_id,data.page,data.extraction_hash,data.state,data.reason,actor(request))

    @app.post(prefix + '/pdf-excel-preview')
    def pdf_excel_preview(org:int,data:PDFCompare):
        with db.connect() as conn:
            gate(org,conn)
            return pdf_call(pdf_review.compare_excel,conn,org,data.pdf_id,data.excel_id,data.sheet)

    @app.get(prefix + '/status')
    def status(org: int):
        with db.connect() as conn:
            gate(org, conn)
        return dict(feature_enabled=True, operation_mode='REVIEW_ONLY', profiles=s.PROFILES, policies=s.POLICIES)

    @app.get(prefix + '/facilities')
    def facilities(org: int):
        with db.connect() as conn:
            gate(org, conn)
            return s.list_rows(conn, s.facilities, org)

    @app.post(prefix + '/facilities')
    def facility(org: int, data: Facility, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org, conn)
            return dict(id=s.add_facility(conn, org, data.model_dump(), actor(request)))

    @app.get(prefix + '/batches')
    def batches(org: int):
        with db.connect() as conn:
            gate(org, conn)
            return [dict(row, quantity=s.text_quantity(row['quantity'])) for row in s.list_rows(conn, s.batches, org)]

    @app.post(prefix + '/batches')
    def batch(org: int, data: Batch, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org, conn)
            return dict(id=s.add_batch(conn, org, data.model_dump(), actor(request)))

    @app.post(prefix + '/evidence')
    async def evidence(org: int, request: Request, name: str = 'evidence'):
        with db.connect() as conn:
            gate(org, conn)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 20_000_000:
                raise HTTPException(413, '증빙은 20MB 이하여야 합니다.')
        if not content or len(name) > 150 or '/' in name or '\\' in name or '..' in name:
            raise HTTPException(422, '증빙 이름과 내용을 확인하세요.')
        sha = hashlib.sha256(content).hexdigest()
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org, conn)
            old = conn.execute(s.evidence.select().where(s.evidence.c.org == org, s.evidence.c.sha == sha)).mappings().first()
            if old:
                return dict(id=old['id'], sha=sha, reused=True)
            entity_id = conn.execute(s.evidence.insert().values(org=org, sha=sha, name=name, content=bytes(content), data=dict(status='UNVERIFIED', uploaded=store.now()))).inserted_primary_key[0]
            store.log(conn, org, actor(request), 'sorting_evidence', entity_id, '증빙 등록', None, dict(sha=sha))
            return dict(id=entity_id, sha=sha, reused=False)

    @app.get(prefix + '/evidence/{entity_id}')
    def original(org: int, entity_id: int):
        with db.connect() as conn:
            gate(org, conn)
            row = s.scoped(conn, s.evidence, org, entity_id)
            return Response(row['content'], media_type='application/octet-stream', headers={'Content-Disposition':"attachment; filename*=UTF-8''evidence.bin"})

    @app.post(prefix + '/import-preview')
    def preview(org: int, evidence_id: int, sheet: str | None = None):
        with db.connect() as conn:
            gate(org, conn)
            content = s.scoped(conn, s.evidence, org, evidence_id)['content']
        try:
            return books.preview(content, sheet)
        except (ValueError, KeyError, OSError, BadZipFile) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(prefix + '/template')
    def template(org: int):
        with db.connect() as conn:
            gate(org, conn)
        return Response(books.template(), media_type=media, headers={'Content-Disposition':"attachment; filename*=UTF-8''sorting-review-template.xlsx"})

    @app.post(prefix + '/import-commit')
    def commit(org:int, data:ImportSelection, request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(batch_ids=s.commit_import(conn,org,data.evidence_id,data.sheet,data.facility_id,data.product,data.rows,actor(request)))

    def reconcile_sources(org,data):
        with db.connect() as conn:
            gate(org,conn)
            try:
                return reconciliation.from_evidence(conn,org,**data.model_dump())
            except (ValueError,KeyError,OSError,BadZipFile) as exc:
                if isinstance(exc,s.SortingError):
                    raise
                raise HTTPException(422,'대사할 Excel과 공식 세부 시트를 확인하세요.') from exc

    @app.post(prefix + '/reconcile-preview')
    def reconcile_preview(org:int,data:Reconciliation):
        return reconcile_sources(org,data)

    @app.post(prefix + '/reconcile-report')
    def reconcile_report(org:int,data:Reconciliation):
        return Response(reconciliation.csv_report(reconcile_sources(org,data)),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':"attachment; filename*=UTF-8''sorting-reconciliation-review.csv"})

    @app.post(prefix + '/compound-preview')
    def compound(org:int,evidence_id:int):
        with db.connect() as conn:
            gate(org,conn)
            source=s.scoped(conn,s.evidence,org,evidence_id)
            content=source['content']
            chunks=[r['data'] for r in s.list_rows(conn,s.records,org) if r['kind']=='COMPOUND_CHUNK' and r['data']['evidence_id']==evidence_id and r['data']['sha']==source['sha']]
        def load_chunk(start):
            return next((r['pages'] for r in chunks if r['start']==start and r.get('version')==sorting_documents.ocr.EXTRACTION_VERSION),None)
        def save_chunk(start,pages):
            with db.begin() as conn:
                conn.execute(s.records.insert().values(org=org,kind='COMPOUND_CHUNK',revision=1,data=dict(evidence_id=evidence_id,sha=source['sha'],start=start,pages=pages,version=sorting_documents.ocr.EXTRACTION_VERSION)))
        try:
            return sorting_documents.extract(content,load_chunk,save_chunk)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post(prefix + '/intakes')
    def intake(org:int,data:Intake,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(id=production.intake(conn,org,data.model_dump(),actor(request)))

    @app.get(prefix + '/intake-import-columns')
    def intake_columns(org:int,evidence_id:int,encoding:Literal['utf-8-sig','cp949']='utf-8-sig'):
        with db.connect() as conn:
            gate(org,conn)
            return intake_import.columns(conn,org,evidence_id,encoding)

    @app.post(prefix + '/intake-import-preview')
    def intake_preview(org:int,data:IntakeMapping):
        with db.connect() as conn:
            gate(org,conn)
            return intake_import.preview(conn,org,**data.model_dump())

    @app.get(prefix + '/intake-import-history')
    def intake_history(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return intake_import.history(conn,org)

    @app.post(prefix + '/intake-import-commit')
    def intake_commit(org:int,data:IntakeCommit,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return intake_import.commit(conn,org,data.model_dump(),actor(request))

    @app.get(prefix + '/production-ledger')
    def production_ledger(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return production.ledger(conn,org)

    @app.post(prefix + '/transforms')
    def transform(org:int,data:Transform,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return production.transform(conn,org,data.model_dump(),actor(request))

    @app.post(prefix + '/dispatches')
    def dispatch(org:int,data:Dispatch,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(id=production.dispatch(conn,org,data.model_dump(),actor(request)))

    @app.get(prefix + '/production-metrics')
    def metrics(org:int):
        from decimal import Decimal
        with db.connect() as conn:
            gate(org,conn)
            records=s.list_rows(conn,s.records,org)
        transforms=[r['data'] for r in records if r['kind']=='LOT_TRANSFORM']
        processed=sum((sum((s.decimal(p['quantity']) for p in r['inputs']),Decimal(0)) for r in transforms),Decimal(0))
        suitable=sum((s.decimal(r['export_quantity']) for r in transforms),Decimal(0))
        return dict(label='검토용 통계 / 공식 KPI 아님',unit='kg',period='등록 전체 기간',
            export_suitability=dict(numerator=str(suitable),denominator=str(processed),
                ratio=None if processed==0 else str(suitable/processed),
                definition='각 선별 작업의 수출 적합량 / 각 작업 투입량; 재선별 작업을 각각 포함하며 실수출률이 아님'),
            contract_fulfillment=None,joint_sorting_rate=None,trade_settlement_rate=None,
            missing_basis=['계약별 실제 수출이행 연결','중복 없는 생산 원물 분모','정산 상태의 정책 기준'])

    @app.get(prefix + '/review-dashboard')
    def review_dashboard(org:int,start:str,end:str,as_of:str,facility_id:int|None=None):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN')
            gate(org,conn)
            return sorting_dashboard.dashboard(conn,org,start,end,as_of,facility_id)

    @app.get(prefix + '/cost-review')
    def cost_review(org:int,start:str,end:str,facility_id:int|None=None):
        with db.connect() as conn:
            conn.exec_driver_sql('BEGIN')
            gate(org,conn)
            return sorting_cost_review.review(conn,org,start,end,facility_id)

    @app.get(prefix + '/candidates')
    def candidates(org: int, number: str):
        with db.connect() as conn:
            gate(org, conn)
            return s.candidates(conn, org, number[:100])

    @app.get(prefix + '/capacity')
    def capacity(org:int,declaration_id:int,declaration_revision:int,batch_id:int,dispatch_id:int|None=None):
        with db.connect() as conn:
            gate(org,conn)
            return s.capacity(conn,org,declaration_id,declaration_revision,batch_id,dispatch_id)

    @app.get(prefix + '/dispatches')
    def dispatch_list(org:int,batch_id:int):
        with db.connect() as conn:
            gate(org,conn);s.scoped(conn,s.batches,org,batch_id)
            return [r for r in production.production_rows(conn,org,'DISPATCH') if r['data']['batch_id']==batch_id]

    @app.post(prefix + '/simulate')
    def simulate(org: int, data: Calculation):
        with db.connect() as conn:
            gate(org, conn)
        return s.reference_amount(**data.model_dump())

    @app.post(prefix + '/form-simulate')
    def form_simulate(org:int,data:FormCalculation):
        with db.connect() as conn:
            gate(org,conn)
        if data.form_version not in {'2026-09-01','LEGACY_DISTRIBUTION'}:
            raise HTTPException(422,'확인된 2026 서식 버전을 선택하세요.')
        value=finance.form_amount(data.total,data.unit_price,data.model_dump()['benefits'])
        return dict(value,form_version=data.form_version,label='검토용 / 지급 미확정',
            business_codes=dict(B='생산유통통합조직',C='수출통합조직',D='전문단지',E='기타 유사 사업'),
            legacy_mapping='구양식 유통조성처 명칭 및 공동물류비 오기는 공동선별비 검토항목으로만 매핑')

    @app.get(prefix + '/claims')
    def claims(org: int):
        with db.connect() as conn:
            gate(org, conn)
            return s.list_rows(conn, s.claims, org)

    @app.get(prefix + '/expenses')
    def expenses(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return [dict(r,reference_paid_cost=str(finance.paid_cost(conn,org,r['id']))) for r in s.list_rows(conn,s.expenses,org)]

    @app.get(prefix + '/records')
    def records(org:int):
        with db.connect() as conn:
            gate(org,conn)
            return [dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,
                s.records.c.kind.not_in([pdf_review.CACHE,'COMPOUND_CHUNK',sorting_snapshots.KIND])).order_by(s.records.c.id)).mappings()]

    @app.get(prefix + '/certificates')
    def certificates(org:int):
        with db.connect() as conn:
            gate(org,conn)
            rows=[dict(r) for r in conn.execute(s.records.select().where(s.records.c.org==org,s.records.c.kind=='CERTIFICATE').order_by(s.records.c.id)).mappings()]
            superseded={r['data'].get('previous_id') for r in rows}
            return [dict(r,superseded=r['id'] in superseded) for r in rows]

    @app.post(prefix + '/records')
    def record(org:int,data:ReviewRecord,request:Request):
        if len(json.dumps(data.data))>20_000:
            raise HTTPException(413,'검토 기록 크기를 초과했습니다.')
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            try:
                entity_id=sorting_records.append(conn,org,data.kind,data.data,actor(request),data.previous_id)
            except (KeyError,TypeError) as exc:
                raise HTTPException(422,'검토 기록 필수값의 형식을 확인하세요.') from exc
            return dict(id=entity_id)

    @app.post(prefix + '/expenses')
    def expense(org:int,data:Expense,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(id=finance.add_expense(conn,org,data.model_dump(),actor(request)))

    @app.post(prefix + '/cost-payment-history')
    def cost_payment(org:int,data:PaymentHistory,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(id=finance.record_payment(conn,org,data.model_dump(),actor(request)))

    @app.post(prefix + '/claims/{entity_id}/cost-allocation')
    def cost_allocation(org:int,entity_id:int,data:CostAllocation,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return dict(id=finance.allocate_cost(conn,org,entity_id,data.expense_id,data.amount,actor(request)))

    @app.post(prefix + '/claims')
    def reserve(org: int, data: Claim, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org, conn)
            return dict(id=s.reserve(conn, org, data.model_dump(), actor(request)))

    @app.get(prefix + '/claims/{entity_id}')
    def review(org: int, entity_id: int):
        with db.connect() as conn:
            gate(org, conn)
            return s.review(conn, org, entity_id)

    @app.post(prefix + '/claims/{entity_id}/transition')
    def transition(org: int, entity_id: int, data: Transition, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org, conn)
            return dict(state=s.transition(conn, org, entity_id, data.revision, data.action, data.reason, actor(request)))

    @app.get(prefix + '/claims/{entity_id}/report')
    def report(org: int, entity_id: int, official: bool = False):
        with db.connect() as conn:
            gate(org, conn)
            if official:
                s.operation_guard()
            result = s.review(conn, org, entity_id)
        return Response(books.report(result), media_type=media, headers={'Content-Disposition':"attachment; filename*=UTF-8''sorting-review.xlsx"})

    @app.get(prefix + '/claims/{entity_id}/print')
    def print_review(org:int,entity_id:int,official:bool=False):
        with db.connect() as conn:
            gate(org,conn)
            if official:
                s.operation_guard()
            result=s.review(conn,org,entity_id)
        return Response(sorting_print.render(result),media_type='text/html; charset=utf-8',
                        headers={'Content-Disposition':"inline; filename*=UTF-8''sorting-review-forms.html"})

    @app.post(prefix + '/claims/{entity_id}/lines/{line_id}')
    def line_transition(org:int,entity_id:int,line_id:int,data:Transition,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return s.line_transition(conn,org,entity_id,line_id,data.revision,data.action,data.reason,actor(request))

    @app.post(prefix + '/claims/{entity_id}/lines/{line_id}/evidence-review')
    def evidence_review(org:int,entity_id:int,line_id:int,data:EvidenceReview,request:Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            gate(org,conn)
            return linked.record(conn,org,entity_id,line_id,data.model_dump(),actor(request))

    @app.get(prefix + '/claims/{entity_id}/lines/{line_id}/evidence-review')
    def evidence_review_history(org:int,entity_id:int,line_id:int):
        with db.connect() as conn:
            gate(org,conn)
            s.scoped(conn,s.claims,org,entity_id)
            line=s.scoped(conn,s.allocations,org,line_id)
            if line['claim']!=entity_id:
                raise HTTPException(404,'현재 신청의 행이 아닙니다.')
            return [r for r in linked.history(conn,org) if r['data']['line_id']==line_id]

    @app.post(prefix + '/claims/{entity_id}/{action}')
    def operational(org: int, entity_id: int, action: str):
        with db.connect() as conn:
            gate(org, conn)
            s.scoped(conn, s.claims, org, entity_id)
            s.operation_guard()


def create_legacy_app(path):
    from epms.api import create_app
    from epms import rules
    from sqlalchemy import select
    app=create_app(path)
    current=list(app.router.routes)
    app.router.routes.clear()
    def organization(conn,org):
        name=conn.execute(select(store.organizations.c.name).where(store.organizations.c.id==org)).scalar()
        if name is None:raise HTTPException(404,'통합조직을 선택하세요.')
        return name
    def actor(request):
        return rules.clean(request.headers.get('x-epms-actor'))[:80] or os.environ.get('USERNAME','로컬 사용자')
    install(app,app.state.db,organization,actor)
    app.router.routes.extend(current)
    return app
