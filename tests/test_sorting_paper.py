from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from openpyxl import load_workbook
from epms import sorting as s,store
from test_sorting import setup,payload
from test_sorting_beneficiaries import save as save_beneficiary
from test_sorting_payment_preparation import seed,create,edit


def submitted(setup):
    app,client,base,evidence,*_=setup;claim,row,statement=seed(setup)
    prep=create(client,base,claim,statement).json()['id']
    assert edit(client,base,prep,'DRAFT',prepared_amount='1000000',basis='합성 상신 제안액').status_code==200
    body=dict(preparation_id=prep,preparation_revision=2,document_number='합성-상신-001',submitted_date='2026-03-10',policy_basis='합성 외부 기준·미확정 사항 명시',reason='합성 상신',request_key='paper-submission-001')
    response=client.post(base+'/paper-cases',json=body);assert response.status_code==200,response.text
    return claim,row,response.json()['id'],body


def decide(client,base,entity,row,evidence,amount='900000',key='paper-decision-001',**changes):
    data=dict(previous_id=None,document_number='합성-결재-001',decision_date='2026-03-11',evidence_id=evidence,decision_basis='합성 기관 결재 근거',change_note='',entry_correction=False,
        members=[dict(member_id=row['member_id'],result='APPROVED',amount=amount,difference_reason='합성 감액/변경 사유')],reason='합성 외부 결재 결과',request_key=key)
    data.update(changes)
    return client.post(base+f'/paper-cases/{entity}/decisions',json=data)


def pay(client,base,entity,decision,row,evidence,amount='600000',key='paper-bank-001',transaction='BANK-001',**changes):
    data=dict(account_reference='SYNTHETIC-ACCOUNT',transaction_id=transaction,paid_date='2026-03-12',bank_amount=amount,status='SUCCESS',evidence_id=evidence,retry_of=None,replaces_payment_id=None,source_change_note='',
        lines=[dict(case_id=entity,decision_id=decision,member_id=row['member_id'],amount=amount)],reason='합성 외부 지급 결과',request_key=key)
    data.update(changes)
    return client.post(base+'/paper-bank-results',json=data)


def adjustment(client,base,payment,evidence,entity,row,action='REFUND',amount='100000',key='paper-refund-001',**changes):
    data=dict(action=action,date='2026-03-13',evidence_id=evidence,account_reference='SYNTHETIC-ACCOUNT',transaction_id='REFUND-001',bank_amount=amount,
        lines=[dict(case_id=entity,member_id=row['member_id'],amount=amount)],reason='합성 환수/입력 정정',request_key=key)
    data.update(changes)
    return client.post(base+f'/paper-bank-results/{payment}/adjustments',json=data)


def test_paper_flow_partial_payment_caps_immutable_submission_and_report(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,body=submitted(setup)
    assert client.post(base+'/paper-cases',json=body).json()['id']==entity
    original=client.get(base+f'/paper-cases/{entity}/report?kind=SUBMISSION').content
    decision=decide(client,base,entity,row,evidence).json()['id']
    payment=pay(client,base,entity,decision,row,evidence);assert payment.status_code==200,payment.text
    assert pay(client,base,entity,decision,row,evidence).json()==payment.json()
    state=client.get(base+f'/paper-cases/{entity}').json()
    member=state['members'][0]
    assert member['prepared_amount']=='1000000' and member['external_approved_amount']=='900000'
    assert member['recorded_paid_amount']=='600000' and member['remaining_to_decision']=='300000'
    assert not state['system_approval'] and not state['bank_verified']
    assert pay(client,base,entity,decision,row,evidence,amount='300001',key='too-much',transaction='BANK-002').status_code==409
    assert pay(client,base,entity,decision,row,evidence,key='duplicate-bank-key').status_code==409
    # Content, not ZIP timestamp, is the immutable submission assertion.
    def contents(data):
        book=load_workbook(BytesIO(data));result=[[cell.value for cells in sheet for cell in cells] for sheet in book];book.close();return result
    assert contents(original)==contents(client.get(base+f'/paper-cases/{entity}/report?kind=SUBMISSION').content)
    html=client.get(base+f'/paper-cases/{entity}/print?kind=RESULT').text
    assert '600,000' in html and '합성-결재-001' in html and 'BANK-001' in html
    assert client.post(base+f'/claims/{claim}/approve').status_code==403
    assert client.get(base+f'/paper-cases/{entity}/report?official=true').status_code==403


def test_submission_locks_claim_line_and_beneficiary_release_then_explicit_withdrawal(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    assert client.post(base+f'/claims/{claim}/transition',json=dict(revision=2,action='WITHDRAW',reason='일반 철회')).status_code==409
    assert client.post(base+f'/claims/{claim}/lines/{row["line_id"]}',json=dict(revision=2,action='REJECT',reason='일반 행 반려')).status_code==409
    assert save_beneficiary(client,base,claim,[],revision=2,key='clear-paper-attribution').status_code==409
    close=dict(date='2026-03-11',evidence_id=evidence,reason='합성 서면 상신 취하 근거',request_key='paper-close-001')
    assert client.post(base+f'/paper-cases/{entity}/close',json=close).status_code==200
    assert client.get(base+f'/paper-cases/{entity}').json()['closures'][0]['proof']['id']==evidence
    assert client.get(base+f'/claims/{claim}').json()['quantity']=='600'
    assert client.post(base+f'/claims/{claim}/transition',json=dict(revision=2,action='WITHDRAW',reason='취하 후 명시적 예약 해제')).status_code==200


def test_refund_does_not_release_resource_or_allow_old_decision_repayment(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    decision=decide(client,base,entity,row,evidence).json()['id'];payment=pay(client,base,entity,decision,row,evidence).json()['id']
    refund=adjustment(client,base,payment,evidence,entity,row);assert refund.status_code==200,refund.text
    assert adjustment(client,base,payment,evidence,entity,row).json()==refund.json()
    assert adjustment(client,base,payment,evidence,entity,row,key='duplicate-refund').status_code==409
    member=client.get(base+f'/paper-cases/{entity}').json()['members'][0]
    assert member['recorded_refund_amount']=='100000' and member['recorded_net_paid']=='500000'
    assert pay(client,base,entity,decision,row,evidence,amount='100000',key='repay-before-paper',transaction='BANK-NEW').status_code==409
    assert client.post(base+f'/paper-cases/{entity}/close',json=dict(date='2026-03-15',evidence_id=evidence,reason='환수 뒤 취하 시도',request_key='paid-case-close')).status_code==409
    revised=decide(client,base,entity,row,evidence,amount='500000',key='revised-decision',previous_id=decision,document_number='합성-변경결재-002')
    assert revised.status_code==200,revised.text
    assert client.get(base+f'/claims/{claim}').json()['quantity']=='600'


def test_failed_retry_and_input_void_replacement_preserve_original_history(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    decision=decide(client,base,entity,row,evidence).json()['id']
    failed=pay(client,base,entity,decision,row,evidence,status='FAILED').json()['id']
    assert client.get(base+f'/paper-cases/{entity}').json()['members'][0]['recorded_paid_amount']=='0'
    success=pay(client,base,entity,decision,row,evidence,key='retry-bank',transaction='BANK-RETRY',retry_of=failed);assert success.status_code==200,success.text
    payment=success.json()['id']
    assert pay(client,base,entity,decision,row,evidence,amount='1',key='overflow-retry',transaction='BANK-RETRY-2',retry_of=failed).status_code==409
    assert adjustment(client,base,payment,evidence,entity,row,action='VOID',key='void-bank-entry').status_code==200
    replaced=pay(client,base,entity,decision,row,evidence,key='replace-bank-entry',transaction='BANK-RETRY',replaces_payment_id=payment)
    assert replaced.status_code==200,replaced.text
    state=client.get(base+f'/paper-cases/{entity}').json()
    assert state['members'][0]['recorded_paid_amount']=='600000' and len(state['payments'])==3 and state['ever_paid']


def test_decision_cas_concurrency_changes_and_payment_limits(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:decide(client,base,entity,row,evidence,key=key),['parallel-paper-1','parallel-paper-2']))
    assert sorted(r.status_code for r in results)==[200,409]
    decision=next(r.json()['id'] for r in results if r.status_code==200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:pay(client,base,entity,decision,row,evidence,key=key,transaction=key),['parallel-bank-1','parallel-bank-2']))
    assert sorted(r.status_code for r in results)==[200,409]
    assert decide(client,base,entity,row,evidence,amount='500000',previous_id=decision,key='too-low-decision',document_number='합성-감액-002').status_code==409


def test_source_change_requires_explicit_note_and_proof_change_is_visible(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    decision=decide(client,base,entity,row,evidence).json()['id']
    with app.state.db.begin() as conn:
        original=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(revision=2,data=dict(original['data'],kg='500')))
    assert pay(client,base,entity,decision,row,evidence).status_code==409
    assert pay(client,base,entity,decision,row,evidence,source_change_note='합성 실제 송금 사실 및 변경 대사 근거').status_code==200
    state=client.get(base+f'/paper-cases/{entity}').json();assert state['source_changed'] and state['decision_source_changed']
    with app.state.db.begin() as conn:conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'changed'))
    assert client.get(base+f'/paper-cases/{entity}').json()['proof_issues']


def test_scope_missing_evidence_amount_sum_and_personal_output(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    assert decide(client,base,entity,row,999999).status_code==404
    decision=decide(client,base,entity,row,evidence).json()['id']
    assert pay(client,base,entity,decision,row,evidence,bank_amount='600001').status_code==409
    assert pay(client,base,entity,decision,row,evidence,amount='0.5').status_code==422
    assert client.get('/api/org/2/sorting/paper-cases/'+str(entity)).status_code==404
    assert client.get(base+f'/paper-cases/{entity}/report?kind=RESULT&member_id=999').status_code==404
    assert client.get(base+f'/paper-cases/{entity}',headers={'x-epms-token':'invalid'}).status_code==403


def test_combined_transfer_multiple_cases_personal_copy_and_duplicate_transaction(setup):
    app,client,base,evidence,facility,batch=setup;claim,row,first,_=submitted(setup)
    with app.state.db.begin() as conn:
        conn.execute(store.companies.insert().values(number='SYNTHETIC-SECOND'))
        other=conn.execute(store.memberships.insert().values(org=1,number='SYNTHETIC-SECOND',revision=1,data=dict(name='다른가상수혜자'))).inserted_primary_key[0]
    second_claim=client.post(base+'/claims',json=payload(batch,'300',key='second-paper-reservation')).json()['id']
    second_line=client.get(base+f'/claims/{second_claim}').json()['lines'][0]['id']
    second_row=dict(row,line_id=second_line,member_id=other,quantity='300')
    assert save_beneficiary(client,base,second_claim,[second_row],key='second-paper-beneficiary').status_code==200
    from test_sorting_beneficiaries import freeze
    statement=freeze(client,base,second_claim,key='second-paper-statement').json()['id']
    prep=create(client,base,second_claim,statement,key='second-paper-prep').json()['id']
    assert edit(client,base,prep,'DRAFT',key='second-paper-prep-edit',prepared_amount='50',basis='합성 제안').status_code==200
    second=client.post(base+'/paper-cases',json=dict(preparation_id=prep,preparation_revision=2,document_number='합성-상신-002',submitted_date='2026-03-10',policy_basis='합성 기준',reason='합성 상신',request_key='second-paper-submit')).json()['id']
    first_decision=decide(client,base,first,row,evidence,amount='100',change_note='추가 예약·회원 자료 대사 근거').json()['id']
    second_decision=decide(client,base,second,second_row,evidence,amount='50',key='second-paper-decision').json()['id']
    lines=[dict(case_id=first,decision_id=first_decision,member_id=row['member_id'],amount='60'),dict(case_id=second,decision_id=second_decision,member_id=other,amount='30')]
    result=pay(client,base,first,first_decision,row,evidence,amount='90',lines=lines);assert result.status_code==200,result.text
    assert client.get(base+f'/paper-cases/{first}').json()['members'][0]['remaining_to_decision']=='40'
    assert client.get(base+f'/paper-cases/{second}').json()['members'][0]['remaining_to_decision']=='20'
    assert pay(client,base,second,second_decision,second_row,evidence,amount='20',key='same-bank-new-case',lines=[dict(case_id=second,decision_id=second_decision,member_id=other,amount='20')]).status_code==409
    report=client.get(base+f'/paper-cases/{first}/report?kind=RESULT&member_id={row["member_id"]}')
    book=load_workbook(BytesIO(report.content));cells=[c.value for sheet in book for r in sheet for c in r];book.close()
    assert '다른가상수혜자' not in str(cells) and 'SYNTHETIC-SECOND' not in str(cells)
    html=client.get(base+f'/paper-cases/{first}/print?kind=RESULT&member_id={row["member_id"]}').text
    assert '다른가상수혜자' not in html


def test_external_decision_entry_correction_holds_and_document_dedup(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    first=decide(client,base,entity,row,evidence).json()['id']
    assert decide(client,base,entity,row,evidence,previous_id=first,key='repeat-document-key').status_code==409
    corrected=decide(client,base,entity,row,evidence,amount='800000',previous_id=first,key='entry-correction-key',entry_correction=True)
    assert corrected.status_code==200,corrected.text
    assert len(client.get(base+f'/paper-cases/{entity}').json()['decisions'])==2
    result=decide(client,base,entity,row,evidence,previous_id=corrected.json()['id'],document_number='합성-보류-003',key='hold-decision-key',members=[dict(member_id=row['member_id'],result='HOLD',amount=None,difference_reason='외부 서면 보류')])
    assert result.status_code==200
    assert pay(client,base,entity,result.json()['id'],row,evidence).status_code==409


def test_refund_entry_void_and_replacement_preserve_all_history(setup):
    app,client,base,evidence,*_=setup;claim,row,entity,_=submitted(setup)
    decision=decide(client,base,entity,row,evidence).json()['id'];payment=pay(client,base,entity,decision,row,evidence).json()['id']
    refund=adjustment(client,base,payment,evidence,entity,row).json()['id']
    corrected=adjustment(client,base,payment,evidence,entity,row,action='VOID_REFUND',key='void-refund-entry',target_adjustment_id=refund)
    assert corrected.status_code==200,corrected.text
    state=client.get(base+f'/paper-cases/{entity}').json()
    assert state['members'][0]['recorded_net_paid']=='600000' and state['members'][0]['recorded_refund_amount']=='0'
    assert state['payments'][0]['adjustments'][0]['entry_voided']
    assert adjustment(client,base,payment,evidence,entity,row,amount='50000',key='refund-silent-reuse').status_code==409
    replacement=adjustment(client,base,payment,evidence,entity,row,amount='50000',key='refund-explicit-replace',replaces_adjustment_id=refund)
    assert replacement.status_code==200,replacement.text
    state=client.get(base+f'/paper-cases/{entity}').json()
    assert state['members'][0]['recorded_net_paid']=='550000' and len(state['payments'][0]['adjustments'])==3
    assert client.post(base+f'/paper-cases/{entity}/close',json=dict(date='2026-03-15',evidence_id=evidence,reason='정정 후 취하 시도',request_key='corrected-case-close')).status_code==409
