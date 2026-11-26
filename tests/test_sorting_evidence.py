"""Evidence linkage acceptance tests; synthetic sources and isolated test databases."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from openpyxl import load_workbook
import pytest
from epms import sorting as s,store
from test_sorting import setup,payload


def prepare(setup,quantity='1000',**overrides):
    app,client,base,evidence,facility,batch=setup
    certificate=dict(facility_id=facility,product='가상품목',quantity=quantity,member_number='1234567834',issuer='합성 사무국',
        start='2026-01-01',end='2026-12-31',duplicate_statement='외부원장 별도 검토',evidence_id=evidence)|overrides
    result=client.post(base+'/records',json=dict(kind='CERTIFICATE',data=certificate))
    assert result.status_code==200,result.text
    claim=client.post(base+'/claims',json=payload(batch)).json()['id']
    line=client.get(f'{base}/claims/{claim}').json()['lines'][0]['id']
    data=dict(revision=1,request_key='evidence-request-1',action='RECORD',actual_shipped='2026-03-02',
        fulfillment_evidence_id=evidence,fulfillment_kind='CUSTOMS_FULFILLMENT',certificate_id=result.json()['id'],reason='합성 원본 대조 기록')
    return claim,line,data


def test_link_review_idempotent_history_revoke_and_export(setup):
    app,client,base,*_=setup
    claim,line,data=prepare(setup)
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    first=client.post(path,json=data)
    assert first.status_code==200,first.text
    assert first.json()['assessment']['status']=='LINKED_FOR_REVIEW'
    assert client.post(path,json=data).json()['id']==first.json()['id']
    review=client.get(f'{base}/claims/{claim}').json()
    assert review['revision']==2 and review['lines'][0]['dates']['actual_shipped']=='2026-03-02'
    assert review['lines'][0]['source']['original'].get('shipped') is None
    assert not any(p['field'] in {'actual_shipped','certificate'} for p in review['lines'][0]['issues'])
    assert review['approved_amount'] is None and review['payable_amount'] is None
    assert any(p['code']=='SORT_E07' for p in review['issues'])
    assert client.post(f'{base}/claims/{claim}/approve').status_code==403
    book=load_workbook(BytesIO(client.get(f'{base}/claims/{claim}/report').content))
    assert book['행별 증빙 검토기록']['B3'].value=='LINKED_FOR_REVIEW'
    book.close()
    assert client.post(path,json=dict(data,reason='동일키 다른 내용')).status_code==409
    revoked=client.post(path,json=dict(revision=2,request_key='revoke-request-1',action='REVOKE',reason='대사 취소'))
    assert revoked.status_code==200
    events=client.get(path).json()
    assert len(events)==2 and events[1]['data']['previous_id']==events[0]['id']
    assert [e['revision'] for e in events]==[1,2]
    assert events[0]['data']['actual_shipped']=='2026-03-02'
    assert client.get(f'{base}/claims/{claim}').json()['lines'][0]['dates']['actual_shipped'] is None


@pytest.mark.parametrize('override',[{'product':'다른 품목'},{'member_number':'0000000000'},{'start':'2026-04-01'}])
def test_certificate_mismatch_is_preserved_as_supplement(setup,override):
    app,client,base,*_=setup
    claim,line,data=prepare(setup,**override)
    response=client.post(f'{base}/claims/{claim}/lines/{line}/evidence-review',json=data)
    assert response.status_code==200,response.text
    assert response.json()['assessment']['certificate_status']=='NEEDS_SUPPLEMENT'
    assert response.json()['assessment']['status']=='NEEDS_SUPPLEMENT'


def test_auxiliary_transport_cannot_confirm_actual_date(setup):
    app,client,base,*_=setup
    claim,line,data=prepare(setup)
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    assert client.post(path,json=dict(data,fulfillment_kind='TRANSPORT_AUXILIARY')).status_code==422
    result=client.post(path,json=dict(data,fulfillment_kind='TRANSPORT_AUXILIARY',actual_shipped=None))
    assert result.status_code==200
    review=client.get(f'{base}/claims/{claim}').json()
    assert review['lines'][0]['dates']['actual_shipped'] is None
    assert review['lines'][0]['evidence_review']['fulfillment_status']=='AUXILIARY_ONLY'
    assert any(i['code']=='SORT_W02' for i in review['lines'][0]['issues'])


def test_source_revision_scope_and_tamper(setup):
    app,client,base,evidence,*_=setup
    claim,line,data=prepare(setup)
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    assert client.post(path,json=data).status_code==200
    assert client.get(f'/api/org/2/sorting/claims/{claim}/lines/{line}/evidence-review').status_code==404
    assert client.post(f'/api/org/2/sorting/claims/{claim}/lines/{line}/evidence-review',json=data).status_code==404
    assert client.post(path,json=dict(data,request_key='stale-evidence-1')).status_code==409
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.update().where(s.evidence.c.id==evidence).values(content=b'tampered'))
    result=client.get(f'{base}/claims/{claim}').json()['lines'][0]
    assert result['evidence_review']['status']=='NEEDS_SUPPLEMENT'
    assert result['dates']['actual_shipped'] is None
    assert any(i['field']=='linked_evidence' for i in result['issues'])
    assert client.post(path,json=dict(data,revision=2,request_key='tampered-request')).status_code==409


def test_certificate_quota_counts_latest_link_once_and_serializes(setup):
    app,client,base,evidence,facility,batch=setup
    claim,line,data=prepare(setup,quantity='700')
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    assert client.post(path,json=data).status_code==200
    replacement=client.post(path,json=dict(data,revision=2,request_key='replacement-request'))
    assert replacement.status_code==200  # Replaces the same 600kg linkage, not another 600kg.
    other=client.post(base+'/claims',json=payload(batch,'400','other-evidence-claim')).json()['id']
    other_line=client.get(f'{base}/claims/{other}').json()['lines'][0]['id']
    result=client.post(f'{base}/claims/{other}/lines/{other_line}/evidence-review',json=dict(data,request_key='quota-excess-1'))
    assert result.status_code==409
    assert client.get(f'{base}/claims/{other}').json()['revision']==1
    assert client.get(f'{base}/claims/{other}/lines/{other_line}/evidence-review').json()==[]
    duplicate=client.post(base+'/records',json=dict(kind='CERTIFICATE',data=dict(facility_id=facility,product='가상품목',quantity='10000',member_number='1234567834',issuer='동일 원본 재등록',start='2026-01-01',end='2026-12-31',duplicate_statement='검토',evidence_id=evidence))).json()['id']
    assert client.post(f'{base}/claims/{other}/lines/{other_line}/evidence-review',json=dict(data,certificate_id=duplicate,request_key='duplicate-quota')).status_code==409


def test_concurrent_certificate_reservations_cannot_exceed_quantity(setup):
    app,client,base,evidence,facility,batch=setup
    certificate=client.post(base+'/records',json=dict(kind='CERTIFICATE',data=dict(facility_id=facility,product='가상품목',quantity='600',member_number='1234567834',issuer='합성 사무국',start='2026-01-01',end='2026-12-31',duplicate_statement='검토',evidence_id=evidence))).json()['id']
    claims=[client.post(base+'/claims',json=payload(batch,'400',f'parallel-claim-{i}')).json()['id'] for i in range(2)]
    lines=[client.get(f'{base}/claims/{claim}').json()['lines'][0]['id'] for claim in claims]
    def link(index):
        return client.post(f'{base}/claims/{claims[index]}/lines/{lines[index]}/evidence-review',json=dict(revision=1,request_key=f'parallel-evidence-{index}',action='RECORD',actual_shipped='2026-03-02',fulfillment_kind='CUSTOMS_FULFILLMENT',fulfillment_evidence_id=evidence,certificate_id=certificate,reason='경쟁 연결 시험')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(link,range(2)))==[200,409]


def test_certificate_replacement_and_record_tamper_are_visible(setup):
    app,client,base,*_=setup
    claim,line,data=prepare(setup)
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    event=client.post(path,json=data).json()['id']
    certificate=client.get(base+'/certificates').json()[0]
    assert client.post(base+'/records',json=dict(kind='CERTIFICATE',previous_id=certificate['id'],data=certificate['data'])).status_code==200
    line_review=client.get(f'{base}/claims/{claim}').json()['lines'][0]
    assert line_review['evidence_review']['certificate_status']=='NEEDS_SUPPLEMENT'
    assert any('새 버전' in i['message'] for i in line_review['issues'])
    assert client.get(base+'/certificates').json()[0]['superseded']
    with app.state.db.begin() as conn:
        row=s.scoped(conn,s.records,1,event)
        conn.execute(s.records.update().where(s.records.c.id==event).values(data=dict(row['data'],actual_shipped='2026-03-03')))
    broken=client.get(f'{base}/claims/{claim}').json()['lines'][0]
    assert broken['dates']['actual_shipped'] is None
    assert any(i['field']=='review_record' for i in broken['issues'])


def test_date_conflict_and_missing_primary_date_remain_supplement(setup):
    app,client,base,*_=setup
    claim,line,data=prepare(setup)
    path=f'{base}/claims/{claim}/lines/{line}/evidence-review'
    response=client.post(path,json=dict(data,actual_shipped='2026-02-28'))
    assert response.status_code==200
    assert response.json()['assessment']['recorded_actual_shipped']=='2026-02-28'
    assert response.json()['assessment']['actual_shipped'] is None
    assert response.json()['assessment']['status']=='NEEDS_SUPPLEMENT'
    assert client.post(path,json=dict(data,revision=2,request_key='invalid-date-key',actual_shipped='2026-02-30')).status_code==422
    assert client.post(path,json=dict(data,revision=2,request_key='invalid-role-key',approved_amount='100')).status_code==422
    missing=client.post(path,json=dict(data,revision=2,request_key='missing-date-key',actual_shipped=None))
    assert missing.status_code==200
    assert missing.json()['assessment']['fulfillment_status']=='NEEDS_SUPPLEMENT'


def test_missing_linked_proof_is_reported_without_losing_review(setup):
    app,client,base,evidence,*_=setup
    claim,line,data=prepare(setup)
    distinct=client.post(base+'/evidence?name=synthetic-fulfillment.txt',content=b'synthetic independent fulfillment proof').json()['id']
    result=client.post(f'{base}/claims/{claim}/lines/{line}/evidence-review',json=dict(data,fulfillment_evidence_id=distinct))
    assert result.status_code==200
    with app.state.db.begin() as conn:
        conn.execute(s.evidence.delete().where(s.evidence.c.id==distinct))
    review=client.get(f'{base}/claims/{claim}')
    assert review.status_code==200
    assert review.json()['lines'][0]['evidence_review']['status']=='NEEDS_SUPPLEMENT'
    assert review.json()['lines'][0]['dates']['actual_shipped'] is None


def test_declared_and_actual_months_stay_separate_without_automatic_attribution(setup):
    app,client,base,*_=setup
    with app.state.db.begin() as conn:
        row=s.scoped(conn,store.declarations,1,1)
        conn.execute(store.declarations.update().where(store.declarations.c.id==1).values(data=dict(row['data'],declared='2026-03-01',shipped='2026-03-15')))
    claim,line,data=prepare(setup)
    result=client.post(f'{base}/claims/{claim}/lines/{line}/evidence-review',json=dict(data,actual_shipped='2026-04-01'))
    assert result.status_code==200
    assert result.json()['assessment']['status']=='LINKED_FOR_REVIEW'
    assert any(i['code']=='SORT_W08' for i in result.json()['assessment']['issues'])
    review=client.get(f'{base}/claims/{claim}').json()
    assert review['lines'][0]['dates']['planned_shipped']=='2026-03-15'
    assert review['lines'][0]['dates']['actual_shipped']=='2026-04-01'
    assert review['data']['year']==2027 and review['payable_amount'] is None
