"""Self-export checks use only isolated synthetic databases."""
import pytest
from epms import rules,contract_gate
from test_settlement import env,source,payload,draft,confirm
from test_member_contracts import payload as contract_payload

def expire_fixture_agreement(env):
    c=env[0]
    value=contract_payload({'id':1},revision=1)
    value.update(end='2026-06-30')
    c.put('/api/org/1/contracts/1',json=value).raise_for_status()

def values(env,owner=1,agent=1):
    p,e=env[5][:2];members={1:p,2:e}
    d=dict(number='12345-26-123456',line='001',version='1',declared='2026-07-02',shipped='2026-07-03',usd='100',kg='1000',hs='0808100000',scope='대상',status='유효')
    for role,mid in [('maker',1),('owner',owner),('agent',agent)]:
        m=members[mid];n=m['number'];n=f'{n[:3]}-{n[3:5]}-{n[5:]}' if role!='maker' else n
        d.update({role:n,role+'_name':m['name'],role+'_state':'회원 대조'})
    return d

@pytest.mark.parametrize('kind',['생산자','복수'])
@pytest.mark.parametrize('owner,agent',[(1,1),(1,2),(2,1)])
def test_self_export_final_excel_catalog_and_settlement(env,kind,owner,agent):
    c=env[0];expire_fixture_agreement(env)
    member=env[5][0]
    if kind=='복수':
        c.post('/api/org/1/data/members',json=[dict(id=member['id'],revision=member['revision'],values=dict(name=member['name'],number=member['number'],kind=kind,approved='2024-01-01'))]).raise_for_status()
    d=values(env,owner,agent)
    saved=c.post('/api/org/1/data/lines',json=[dict(values=d)]).raise_for_status().json()[0]
    assert saved['row'] and not any(i['severity']!='INFO' for i in saved['issues'])
    assert any(i['code']=='D085' and i['severity']=='INFO' for i in saved['issues'])
    line=saved['row']['id'];runs=[]
    for year in [2025,2026]:
        c.put(f'/api/org/1/policies/{year}',json=dict(cutoff=f'{year}-12-31',basis='shipped',hs=['0808100000'],reason='격리된 자기수출 검증')).raise_for_status()
        runs.append(c.post(f'/api/org/1/runs?year={year}').raise_for_status().json()['id'])
    run=c.get(f'/api/org/1/runs/{runs[-1]}').raise_for_status().json();assert run['ready']
    own=next(m for m in run['summary'] if m['number']==member['number']);assert str(own['direct_usd'])=='100' and str(own['indirect_usd'])=='0'
    final=c.post('/api/org/1/annual-submission',params=dict(previous_id=runs[0],run_id=runs[1],final=True)).raise_for_status()
    assert final.content.startswith(b'PK')
    batch,_=source(env)
    catalog=c.get(env[2]+'/catalog').raise_for_status().json()
    declaration=next(v for v in catalog['declarations'] if v['id']==line);assert declaration['errors']==[]
    data=payload(env,[(batch,line,'1000','1000')]);job=draft(env,data);result=confirm(env,job,data).raise_for_status().json()['snapshot']['result']
    assert result['amount']=='5000' and result['errors']==[]
    assert result['lines'][0]['contract_evidence']==[dict(producer_id=1,self_export=True)]
    assert c.get(env[2]+'/catalog').json()['batches'][0]['available']=='0'

def test_equal_names_with_different_business_numbers_are_not_self_export(env):
    a,b=env[5][:2];a=dict(a,name='같은 업체명');b=dict(b,name='같은 업체명')
    row=values(env,2,2);row.update(maker_name=a['name'],owner_name=b['name'],agent_name=b['name'])
    issues,_=contract_gate.evaluate(row,{a['number']:a,b['number']:b},[])
    assert any(i['code']=='D090' for i in issues)

def test_exporter_only_membership_does_not_bypass_manufacturer_validation(env):
    d=values(env);members={m['number']:dict(m,kind='수출업체') if m['id']==1 else m for m in env[5]}
    issues,_=rules.validate_line(d,members)
    assert any(i['code']=='D048' for i in issues)
