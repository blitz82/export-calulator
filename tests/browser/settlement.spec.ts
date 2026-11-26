import {test,expect} from '@playwright/test'
test.skip(process.env.EPMS_SETTLEMENT_E2E!=='1','Requires a disposable settlement database')

function pdf(){
  const stream='BT /F1 18 Tf 30 100 Td (SORTING FIXTURE 1000 KG) Tj ET'
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Count 1 /Kids [3 0 R] >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`]
  let data='%PDF-1.4\n';const offsets:number[]=[];objects.forEach((o,i)=>{offsets.push(Buffer.byteLength(data));data+=`${i+1} 0 obj\n${o}\nendobj\n`});const xref=Buffer.byteLength(data)
  return Buffer.from(data+'xref\n0 6\n0000000000 65535 f \n'+offsets.map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
}

test('PDF evidence → source and paid cost → split connections → saved calculation and PDF',async({page,request})=>{
  test.setTimeout(90000)
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  const token=(await(await request.get('/api/session')).json()).token,headers={'x-epms-token':token}
  expect((await(await request.get('/api/office',{headers})).json()).item).toBeNull()
  expect((await request.post('/api/organizations?name=사과',{headers})).ok()).toBeTruthy()
  const ms=[['생산조합','1234567815','생산자'],['수출회사','1234567829','수출업체']]
  for(const [name,number,kind] of ms){const r=await(await request.post('/api/org/1/data/members',{headers,data:[{values:{name,number,kind,approved:'2024-01-01'}}]})).json();expect(r[0].row).toBeTruthy()}
  const doc=(await(await request.post('/api/org/1/settlements/documents/upload?name=지정.pdf&kind=시설지정',{headers,data:pdf()})).json()).id
  expect((await request.post('/api/org/1/sorting/facilities',{headers,data:{name:'사과 공동선별장',product:'사과',member_number:ms[0][1],designation:'테스트지정',valid_from:'2025-01-01',valid_to:'2027-12-31',evidence_id:doc}})).ok()).toBeTruthy()
  const contractDoc=(await(await request.post('/api/org/1/contracts/documents/upload?name=계약.pdf',{headers,data:pdf()})).json()).id
  expect((await request.post('/api/org/1/contracts',{headers,data:{title:'수출 계약',kind:'공급',producer_id:1,exporter_id:2,document_id:contractDoc,signed:'2025-01-01',start:'2025-01-01',end:'2027-12-31',reason:'합성 검증'}})).ok()).toBeTruthy()
  for(const [line,kg] of [['001','600'],['002','400']]){
    const values={number:'12345-26-123456',line,version:'1',declared:'2026-07-02',shipped:'2026-07-03',usd:'100',kg,hs:'0808100000',scope:'대상',status:'유효',maker:ms[0][1],maker_name:ms[0][0],maker_state:'회원 대조',owner:ms[1][1],owner_name:ms[1][0],owner_state:'회원 대조',agent:ms[1][1],agent_name:ms[1][0],agent_state:'회원 대조'}
    expect((await(await request.post('/api/org/1/data/lines',{headers,data:[{values}]})).json())[0].row).toBeTruthy()
  }
  // Intake entry has moved to its independent menu; this test exercises native legacy records in the new board.
  const source=(await(await request.post('/api/org/1/settlements/sources',{headers,data:{facility_id:1,title:'7월 선별작업',external_id:'7월 1일 1차',period_type:'일별',start:'2026-07-01',end:'2026-07-01',producer_id:1,export_quantity:'1000',evidence:[{file:doc,page:1}],confirmed:true}})).json()).id
  expect((await request.post('/api/org/1/settlements/costs',{headers,data:{facility_id:1,title:'7월 선별 급여',external_id:'7월 급여 이체',start:'2026-07-01',end:'2026-07-31',gross:'10000',payments:[{reference:'7월31일 급여',date:'2026-07-31',amount:'10000'}],evidence:[{file:doc,page:1}],batch_ids:[source],confirmed:true}})).ok()).toBeTruthy()
  await page.goto('/#/sorting')
  await expect(page.getByRole('heading',{name:'공동선별비 정산',exact:true})).toBeVisible()
  await page.getByLabel('정산 제목',{exact:true}).fill('7월 공동선별비 정산')
  await page.getByLabel('사업연도',{exact:true}).fill('2026')
  await page.getByRole('combobox',{name:'정산 공동선별장',exact:true}).selectOption('1')
  await page.getByLabel('대상 시작일',{exact:true}).fill('2026-07-01')
  await page.getByLabel('대상 종료일',{exact:true}).fill('2026-07-31')
  await page.getByRole('button',{name:'작업 생성',exact:true}).click()
  await page.getByRole('tab',{name:'물량 연결·계산',exact:true}).click()
  const left=page.getByRole('button',{name:'선별 7월 선별작업 연결',exact:true})
  await left.click();await page.getByRole('button',{name:'신고 12345-26-123456 / 001 연결',exact:true}).click()
  // A second link is made with the native drag path.
  await page.getByRole('button',{name:'7월 선별작업 연결점',exact:true}).dragTo(page.getByRole('button',{name:'12345-26-123456 / 002 연결점',exact:true}))
  await expect(page.getByRole('button',{name:'연결 2 400 kg 대 400 kg 수정',exact:true})).toBeVisible()
  await left.hover();await page.screenshot({path:'output/settlement-connections.png',fullPage:true})
  await page.getByRole('button',{name:'원문 보기',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'선별 증빙 원문 대조',exact:true})).toBeVisible()
  await page.getByRole('dialog',{name:'선별 증빙 원문 대조',exact:true}).getByRole('button',{name:'닫기',exact:true}).click()
  await page.getByRole('button',{name:'급여·비용 배부 설정',exact:true}).click()
  await page.getByRole('combobox',{name:'배부할 비용',exact:true}).selectOption('1')
  await page.getByLabel('배부액 원',{exact:true}).fill('10000')
  await page.getByLabel('배부 근거·사유',{exact:true}).fill('수출 작업 직접 확인')
  await page.getByRole('button',{name:'계산 기준 수정',exact:true}).click()
  await page.getByLabel('kg당 단가 (원)',{exact:true}).fill('10')
  await page.getByLabel('보조율 (%)',{exact:true}).fill('50')
  await page.getByRole('checkbox',{name:'가정값과 연결별 양쪽 물량 중 작은 값의 합계를 사용하는 참고 계산임을 확인했습니다.',exact:true}).check()
  await expect(page.getByRole('button',{name:'연결 확정·참고 계산 보존',exact:true})).toBeEnabled()
  await page.getByRole('button',{name:'연결 확정·참고 계산 보존',exact:true}).click()
  await expect(page.getByRole('button',{name:'결과 PDF 다운로드',exact:true})).toBeVisible()
  await expect(page.getByText('5000원',{exact:true})).toBeVisible()
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'결과 PDF 다운로드',exact:true}).click()
  const downloaded=await pending;await downloaded.saveAs('output/pdf/공동선별비_브라우저검증.pdf')
  await page.screenshot({path:'output/settlement-result.png',fullPage:true})
  const catalog=await(await request.get('/api/org/1/settlements/catalog',{headers})).json()
  expect(catalog.batches[0].available).toBe('0');expect(catalog.declarations.every((d:{available:string})=>d.available==='0')).toBeTruthy()
  await page.reload();await page.getByRole('combobox',{name:'저장한 정산 작업',exact:true}).selectOption('1')
  await page.getByRole('tab',{name:'정산 결과',exact:true}).click()
  await expect(page.getByRole('button',{name:'보존 PDF 다운로드',exact:true})).toBeVisible()
  await page.setViewportSize({width:820,height:950})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
})
