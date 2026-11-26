"""Create a disposable historical DB; the browser server uses only production APIs."""
import argparse,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from legacy_sorting_fixture import create_legacy_app
from test_ocr import searchable_pdf

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);args=parser.parse_args()
    target=Path(args.db).resolve();workspace=Path(__file__).resolve().parents[1]
    if target.exists() or not target.is_relative_to(workspace/'output'):raise SystemExit('Use a new DB path inside workspace/output only.')
    os.environ['EPMS_SORTING_ENABLED']='1';app=create_legacy_app(target)
    try:
        with TestClient(app) as c:
            c.headers['x-epms-token']=c.get('/api/session').json()['token']
            c.post('/api/organizations?name=사과').raise_for_status();base='/api/org/1/sorting'
            for name,number,kind in [('생산조합','1234567815','생산자'),('수출회사','1234567829','수출업체')]:
                assert c.post('/api/org/1/data/members',json=[dict(values=dict(name=name,number=number,kind=kind,approved='2024-01-01'))]).json()[0]['row']
            evidence=c.post(base+'/evidence?name=합성증빙.pdf',content=searchable_pdf()).raise_for_status().json()['id']
            facility=c.post(base+'/facilities',json=dict(name='사과 공동선별장',product='사과',member_number='1234567815',designation='TEST',valid_from='2025-01-01',valid_to='2027-12-31',evidence_id=evidence)).raise_for_status().json()['id']
            batch=c.post(base+'/batches',json=dict(facility_id=facility,external_id='종전 7월 선별',work_date='2026-07-01',product='사과',quantity='1000',unit='kg',evidence_id=evidence)).raise_for_status().json()['id']
            values=dict(number='12345-26-123456',line='001',version='1',declared='2026-07-02',shipped='2026-07-03',usd='100',kg='1000',hs='0808100000',scope='대상',status='유효',maker='1234567815',maker_name='생산조합',owner='1234567829',owner_name='수출회사',agent='1234567829',agent_name='수출회사')
            row=c.post('/api/org/1/data/lines',json=[dict(values=values)]).raise_for_status().json()[0]['row']
            claim=c.post(base+'/claims',json=dict(request_key='archive-browser-fixture',year=2027,profile='INTEGRATED_2027_DRAFT',product='사과',lines=[dict(declaration_id=row['id'],declaration_revision=1,batch_id=batch,batch_revision=1,quantity='600')])).raise_for_status().json()['id']
            cost=c.post(base+'/expenses',json=dict(facility_id=facility,external_id='종전 급여',month='2026-07',category='EXPORT_SORTING_LABOR',amount='1000',separated=True,evidence_id=evidence,separation_evidence_id=evidence,pseudonym='합성 작업자')).raise_for_status().json()['id']
            c.post(base+'/cost-payment-history',json=dict(expense_id=cost,transaction_id='ARCHIVE-BANK',status='SUCCESS',amount='1000',date='2026-07-31',evidence_id=evidence)).raise_for_status()
            c.post(base+f'/claims/{claim}/cost-allocation',json=dict(expense_id=cost,amount='400')).raise_for_status()
        print('Created isolated archive fixture with 600 kg and 400 KRW reserved.')
    finally:app.state.db.dispose()

if __name__=='__main__':main()
