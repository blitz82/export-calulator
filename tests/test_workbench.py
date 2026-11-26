from io import BytesIO
from zipfile import ZipFile
from test_settlement import env,source,declaration,payload,draft,confirm
from test_monthly_report import report_data,create,freeze

def test_workspace_month_preferences_and_scope(env):
    c=env[0];path='/api/org/1/workspace'
    r=c.get(path+'?month=2026-07').raise_for_status().json();assert r['jobs']==[] and r['start']=='2026-07-01'
    assert c.get(path+'?month=2026-99').status_code==422
    prefs=c.put(path+'/preferences',json=dict(writer='사무국',destination='aT 테스트 지역본부',mapping={'number':0})).raise_for_status().json()
    assert prefs['revision']==1 and prefs['data']['writer']=='사무국'
    assert c.put(path+'/preferences',json=dict(writer='다른 창',revision=0)).status_code==409
    report_data(env)
    assert len(c.get(path+'?month=2026-07').json()['jobs'])==1

def test_payment_statement_exact_identity_manual_match_and_archive(env):
    data,*_=report_data(env);c=env[0];path='/api/org/1/workspace'
    number=env[5][0]['number']
    raw=('사업자번호,업체명,지급일,거래번호,수취인,지급액\n'+number+',생산조합,2026-08-03,송금1,생산조합,"5,000"\n').encode('utf-8-sig')
    uploaded=c.post(path+'/imports/upload?name=지급명세.csv',content=raw).raise_for_status().json()
    assert c.post(path+'/imports/upload?name=다른파일명.csv',content=raw).json()['id']==uploaded['id']
    mapping=dict(number=0,name=1,date=2,reference=3,recipient=4,amount=5)
    preview=c.post(path+f"/imports/{uploaded['id']}/preview",json=dict(mapping=mapping)).raise_for_status().json()
    assert preview['errors']==[] and preview['payments'][0]['amount']=='5000'
    data['payments']=[dict(preview['payments'][0],evidence=[dict(file=env[3],page=1)])]
    row=create(env,data);freeze(env,row).raise_for_status()
    archive=ZipFile(BytesIO(c.get(f"/api/org/1/monthly-reports/{row['id']}/pdf?originals=true").content))
    assert any(n.startswith('지급명세/') for n in archive.namelist())
    # Similar names are candidates only, never an automatic identity decision.
    preview=c.post(path+f"/imports/{uploaded['id']}/preview",json=dict(mapping={k:v for k,v in mapping.items() if k!='number'})).json()
    assert preview['rows'][0]['member_id'] is None and preview['errors']
    manual=c.post(path+f"/imports/{uploaded['id']}/preview",json=dict(mapping={k:v for k,v in mapping.items() if k!='number'},overrides={'2':{'member_id':'1'}})).json()
    assert manual['errors']==[]

def test_xlsx_import_refund_and_incomplete_values(env):
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.append(['번호','날짜','참조','수취인','금액']);ws.append([env[5][0]['number'],'2026/08/03','반환1','생산조합',-1000]);buf=BytesIO();wb.save(buf)
    c=env[0];path='/api/org/1/workspace';upload=c.post(path+'/imports/upload?name=명세.xlsx',content=buf.getvalue()).json()
    r=c.post(path+f"/imports/{upload['id']}/preview",json=dict(mapping=dict(number=0,date=1,reference=2,recipient=3,amount=4))).json()
    assert r['errors']==[] and r['payments'][0]['refund'] and r['payments'][0]['amount']=='1000'
    bad=c.post(path+f"/imports/{upload['id']}/preview",json=dict(mapping=dict(number=0))).json();assert bad['errors'] and bad['payments']==[]
    assert c.post(path+'/imports/upload?name=원본.pdf',content=b'%PDF-test').status_code==422

def test_effective_support_periods_and_old_pdf_unchanged(env):
    c=env[0];path='/api/org/1/support-budgets/2026'
    plan=dict(enabled=True,status='가정값',title='2026년 기준',start='2026-01-01',end='2026-12-31',total='100000',unit_price='10',rate_included=True,allocations=[dict(member_id=1,amount='100000')],reason='기간별 검증',date_basis='선별기간',periods=[dict(key='first',title='상반기',start='2026-01-01',end='2026-06-30',unit_price='10'),dict(key='second',title='하반기',start='2026-07-01',end='2026-12-31',unit_price='20')])
    c.put(path,json=plan).raise_for_status()
    b,_=source(env,'100');d=declaration(env,'100');data=payload(env,[(b,d,'100','100')]);data['support_revision']=1
    row=draft(env,data);snap=confirm(env,row,data).raise_for_status().json();assert snap['snapshot']['result']['amount']=='2000'
    assert snap['snapshot']['result']['support']['effective_period']['key']=='second'
    old=c.get(env[2]+f"/results/{snap['id']}/pdf").content
    plan['periods'][1]['unit_price']='30';c.put(path,json=dict(plan,revision=1)).raise_for_status()
    assert c.get(env[2]+f"/results/{snap['id']}/pdf").content==old
    plan['periods'][1]['start']='2026-06-30';assert c.put(path,json=dict(plan,revision=2)).status_code==422
    spanning,_=source(env,'100','단가경계',period_type='월별',start='2026-06-30',end='2026-07-01');decl=declaration(env,'100','002');data=payload(env,[(spanning,decl,'100','100')]);data['support_revision']=2;data['lines'][0]['period_reason']='기간 경계 검증';data['start']='2026-06-30'
    row=draft(env,data);r=confirm(env,row,data);assert r.status_code==422 and '기간별' in r.json()['detail']

def test_normal_monthly_draft_has_automatic_audit_reason(env):
    data,*_=report_data(env);data['reason']='';row=create(env,data);assert row['data']['reason']=='월별 보고 최초 등록'

def test_original_statement_rows_survive_blank_lines_and_selected_import(env):
    c=env[0];path='/api/org/1/workspace';number=env[5][0]['number']
    content=f'번호,일자,참조,수취인,금액\n{number},2026-08-01,첫거래,생산조합,1000\n,,,,\n{number},2026-08-02,둘째거래,생산조합,2000\n'.encode()
    upload=c.post(path+'/imports/upload?name=빈행포함.csv',content=content).raise_for_status().json();assert upload['row_numbers']==[2,4]
    r=c.post(path+f"/imports/{upload['id']}/preview",json=dict(mapping=dict(number=0,date=1,reference=2,recipient=3,amount=4),include_rows=[4])).raise_for_status().json()
    assert [v['row'] for v in r['rows']]==[4] and r['payments'][0]['import_rows']==[4]

def test_malformed_currency_is_not_silently_changed_to_another_amount(env):
    c=env[0];path='/api/org/1/workspace';number=env[5][0]['number']
    raw=f'번호,일자,참조,수취인,금액\n{number},2026-08-03,오류거래,생산조합,"5,00"\n'.encode()
    upload=c.post(path+'/imports/upload?name=잘못된구분.csv',content=raw).json()
    r=c.post(path+f"/imports/{upload['id']}/preview",json=dict(mapping=dict(number=0,date=1,reference=2,recipient=3,amount=4))).json()
    assert r['rows'][0]['amount'] is None and r['errors'] and not r['payments']
