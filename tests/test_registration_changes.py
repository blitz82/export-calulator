from uuid import uuid4
import pytest
from test_settlement import env,source,declaration,payload,draft,confirm


def test_open_ended_facility_create_revise_overlap_and_matching(env):
    c,app,path,doc,f,m,tmp=env
    value=dict(name='종료일 없는 시설',product='사과',member_number=m[0]['number'],designation='무기한',valid_from='2025-01-01',evidence_id=doc)
    created=c.post('/api/org/1/sorting/facilities',json=value).raise_for_status().json()['id']
    row=next(r for r in c.get('/api/org/1/sorting/facilities').json() if r['id']==created)
    assert row['data']['valid_to'] is None
    assert c.post('/api/org/1/sorting/facilities',json=value).status_code==409
    value['valid_to']='2024-01-01'
    assert c.post(f'/api/org/1/sorting/facilities/{created}/revise',json=dict(revision=1,reason='기간 수정',data=value)).status_code==422
    value['valid_to']=''
    c.post(f'/api/org/1/sorting/facilities/{created}/revise',json=dict(revision=1,reason='기간 미정',data=value)).raise_for_status()
    b,_=source(env,facility_id=created)
    d=declaration(env)
    data=payload(env,[(b,d,'1000','1000')]);data['facility_id']=created
    confirm(env,draft(env,data),data).raise_for_status()


@pytest.mark.parametrize('start,ship,allowed',[
    ('2026-07-01','2026-07-01',True),
    ('2026-07-01','2026-08-02',True),
    ('2026-12-31','2027-01-01',True),
    ('2026-07-01','2026-06-30',False),
    ('2026-07-01','',False),
])
def test_shipping_date_invariant_even_with_exception_reason(env,start,ship,allowed):
    c=env[0];b,_=source(env,start=start,end=start);d=declaration(env)
    row=next(r for r in c.get('/api/org/1/data/lines').json() if r['id']==d)
    values={k:v for k,v in row.items() if isinstance(v,str)};values['shipped']=ship
    if ship:values['declared']=min(values['declared'],ship)
    c.post('/api/org/1/data/lines',json=[dict(id=d,revision=row['revision'],values=values)]).raise_for_status()
    data=payload(env,[(b,d,'1000','1000')]);data.update(start=start,end=start)
    data['lines'][0].update(declaration_revision=2,period_reason='' if allowed else '이월 연결 검증')
    job=draft(env,data);result=confirm(env,job,data)
    if allowed:assert result.is_success,result.text
    else:
        assert result.status_code==422
        assert '선적일' in str(result.json())


def test_minimum_manual_source_without_ocr_or_field_evidence(env):
    b,_=source(env)
    row=next(r for r in env[0].get(env[2]+'/catalog?records_only=true').json()['batches'] if r['id']==b)
    assert row['ready'] and not row['data']['field_evidence']
