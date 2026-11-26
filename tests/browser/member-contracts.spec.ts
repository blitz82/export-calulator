import {test,expect} from '@playwright/test'

// Opt in only against an explicitly started, disposable contract validation database.
test.skip(process.env.EPMS_CONTRACT_E2E!=='1','Requires a dedicated contract test database')

function pdf(){
  const stream='BT /F1 18 Tf 30 100 Td (CONTRACT FIXTURE) Tj ET'
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`]
  let data='%PDF-1.4\n';const offsets:number[]=[]
  objects.forEach((body,i)=>{offsets.push(Buffer.byteLength(data));data+=`${i+1} 0 obj\n${body}\nendobj\n`})
  const xref=Buffer.byteLength(data)
  data+='xref\n0 6\n0000000000 65535 f \n'+offsets.map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')
  return Buffer.from(data+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
}

test('register, compare PDF, revise, graph, amendment, member shortcut and responsive layout',async({page,request})=>{
  test.setTimeout(90000)
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message))
  const token=(await (await request.get('/api/session')).json()).token
  const headers={'x-epms-token':token}
  const office=await (await request.get('/api/office',{headers})).json()
  expect(office.item).toBeNull() // Refuse to seed any existing organization.
  expect((await request.post('/api/organizations?name=계약브라우저검증',{headers})).ok()).toBeTruthy()
  const members=[['생산조합 가','1234567814'],['수출회사 나','1234567829'],['수출회사 다','1234567833']]
  // Compute valid registration check digits so the real member validator runs.
  const valid=(s:string)=>{
    const digits=s.slice(0,9).split('').map(Number),weights=[1,3,7,1,3,7,1,3,5]
    const sum=digits.reduce((total,n,i)=>total+n*weights[i],0)+Math.floor(digits[8]*5/10)
    return s.slice(0,9)+((10-sum%10)%10)
  }
  for(const [name,seed] of members){
    const result=await (await request.post('/api/org/1/data/members',{headers,data:[{values:{name,number:valid(seed),approved:'2024-01-01',kind:'생산자'}}]})).json()
    expect(result[0].row).toBeTruthy()
  }
  expect((await request.put('/api/org/1/policies/2026',{headers,data:{cutoff:'2026-12-31',basis:'shipped',hs:['0808100000','0808300000'],reason:'복수 HS 검증'}})).ok()).toBeTruthy()
  expect((await request.put('/api/org/1/policies/2025',{headers,data:{cutoff:'2025-12-31',basis:'shipped',hs:['0808100000'],reason:'확정 차단 검증'}})).ok()).toBeTruthy()
  const line={number:'12345-26-123456',line:'001',version:'1',declared:'2026-07-14',shipped:'2026-07-20',usd:'100',kg:'10',hs:'0808100000',scope:'대상',status:'유효',
    maker:valid(members[0][1]),maker_name:members[0][0],maker_state:'회원 대조',
    agent:valid(members[1][1]),agent_name:members[1][0],agent_state:'회원 대조',
    owner:valid(members[2][1]),owner_name:members[2][0],owner_state:'회원 대조'}
  const registered=await (await request.post('/api/org/1/data/lines',{headers,data:[{values:line}]})).json()
  expect(registered[0].issues.some((p:{code:string})=>p.code==='D090')).toBeTruthy()
  await page.goto('/#/lines')
  await expect(page.getByRole('alert').filter({hasText:'계약 관계가 확인되지 않은 신고'})).toBeVisible()
  await expect(page.getByRole('link',{name:'계약서 등록 확인',exact:true})).toBeVisible()
  await page.goto('/#/submission?year=2026')
  await page.getByRole('button',{name:'현재 자료 검토',exact:true}).click()
  await expect(page.getByRole('button',{name:'최종 확정 후 제출용 Excel 다운로드',exact:true})).toBeDisabled()
  await expect(page.getByRole('alert').filter({hasText:'계약 관계 확인이 필요하여'})).toBeVisible()
  await page.goto('/#/contracts')
  const nav=page.getByRole('navigation',{name:'업무 메뉴'})
  const links=await nav.getByRole('link').allTextContents()
  expect(links.indexOf('계약서 등록')).toBe(links.indexOf('회원사 명부')+1)
  await page.getByRole('button',{name:'계약 신규 등록',exact:true}).click()
  await page.getByLabel('계약명',{exact:true}).fill('사과 공급·수출 계약')
  await page.getByRole('combobox',{name:'생산자회원사 선택',exact:true}).selectOption('1')
  await page.getByRole('combobox',{name:'수출업체 선택',exact:true}).selectOption('2')
  await expect(page.getByRole('combobox',{name:'생산자회원사 선택',exact:true})).not.toContainText('#1')
  await expect(page.getByLabel('통합조직 품목',{exact:true})).toHaveValue('계약브라우저검증')
  await expect(page.getByLabel('통합조직 품목',{exact:true})).toHaveAttribute('readonly','')
  await page.getByLabel('계약 연도',{exact:true}).fill('2026')
  await expect(page.getByLabel('계약 시작일',{exact:true})).toHaveValue('2026-01-01')
  await expect(page.getByLabel('계약 종료일 (미정이면 공란)',{exact:true})).toHaveValue('2026-12-31')
  await expect(page.getByText('대상 HS코드 (2026년 정책):',{exact:false})).toContainText('0808100000, 0808300000')
  await page.getByRole('combobox',{name:'계약 기간 구분',exact:true}).selectOption('기간 지정')
  await page.getByLabel('계약 체결일',{exact:true}).fill('2026-07-15')
  await expect(page.getByLabel('계약 시작일',{exact:true})).toHaveValue('2026-07-15')
  await page.getByLabel('계약 종료일 (미정이면 공란)',{exact:true}).fill('2026-12-31')
  await page.getByRole('textbox',{name:'공급 약정',exact:true}).fill('생산조합이 사과를 공급한다.')
  await page.getByRole('textbox',{name:'대금 지급',exact:true}).fill('검수 후 지급')
  await page.getByLabel('계약 PDF 업로드',{exact:true}).setInputFiles({name:'계약검증.pdf',mimeType:'application/pdf',buffer:pdf()})
  await expect(page.getByRole('img',{name:'계약 원본 1쪽',exact:true})).toBeVisible()
  await page.getByLabel('계약 당사자 근거 쪽',{exact:true}).fill('1')
  await page.getByLabel('대금 지급 근거 쪽',{exact:true}).fill('1')
  await page.getByRole('combobox',{name:'검토 상태',exact:true}).selectOption('확인 완료')
  await page.getByRole('button',{name:'계약 저장',exact:true}).click()
  await expect(page.getByRole('heading',{name:'계약 #1 · 수정 1차',exact:true})).toBeVisible()
  const gate=await (await request.post('/api/org/1/submission-review?year=2026',{headers})).json()
  expect(gate.results.every((r:{ready:boolean})=>r.ready)).toBeTruthy()
  const lineChecks=await (await request.get('/api/org/1/line-checks',{headers})).json()
  expect(Object.values(lineChecks).flat().some(p=>(p as {code:string}).code==='D090')).toBeFalsy()
  await page.getByRole('textbox',{name:'대금 지급',exact:true}).fill('인도 후 30일 이내 지급')
  await page.getByLabel('변경 사유',{exact:true}).fill('원문 대조 후 지급 약정 정정')
  await page.getByRole('button',{name:'계약 저장',exact:true}).click()
  await expect(page.getByRole('heading',{name:'계약 #1 · 수정 2차',exact:true})).toBeVisible()
  await page.getByText('변경 이력과 당시 내용',{exact:true}).click()
  await page.getByRole('button',{name:'당시 내용·PDF 보기',exact:true}).last().click()
  await expect(page.getByRole('region',{name:'과거 계약 원문'})).toContainText('검수 후 지급')
  await page.getByRole('button',{name:'보존본 닫기',exact:true}).click()
  await page.getByRole('button',{name:'입력 닫기',exact:true}).click()
  const saved=(await (await request.get('/api/org/1/contracts',{headers})).json())[0]
  const {producer,exporter,...data}=saved.data
  expect(producer.name).toBe('생산조합 가');expect(exporter.name).toBe('수출회사 나')
  for(const exporter_id of [2,3,1]){
    const result=await request.post('/api/org/1/contracts',{headers,data:{...data,title:`추가약정 ${exporter_id}`,exporter_id,reason:'브라우저 연결 검증'}})
    expect(result.ok()).toBeTruthy()
  }
  await page.reload()
  await page.getByLabel('기준일',{exact:true}).fill('2025-12-31')
  await page.getByRole('combobox',{name:'계약 상태',exact:true}).selectOption('예정')
  await expect(page.getByRole('row').filter({hasText:'사과 공급·수출 계약'})).toContainText('예정')
  await page.getByLabel('기준일',{exact:true}).fill('2026-08-01')
  await page.getByRole('combobox',{name:'계약 상태',exact:true}).selectOption('유효')
  await page.getByRole('tab',{name:'회원사 연결도',exact:true}).click()
  await expect(page.getByRole('button',{name:'생산조합 가와 수출회사 나 계약 2건',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'생산조합 가와 수출회사 다 계약 1건',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'생산조합 가와 생산조합 가 계약 1건',exact:true})).toHaveCount(0)
  const producerNode=page.getByRole('button',{name:'생산자회원사 생산조합 가 연결 강조',exact:true})
  const exporterB=page.getByRole('button',{name:'수출업체 수출회사 나 연결 강조',exact:true})
  const exporterC=page.getByRole('button',{name:'수출업체 수출회사 다 연결 강조',exact:true})
  await producerNode.hover()
  await expect(exporterB.locator('rect')).toHaveAttribute('fill','#ddf0e3')
  await expect(exporterC.locator('rect')).toHaveAttribute('fill','#ddf0e3')
  await exporterB.hover()
  await expect(producerNode.locator('rect')).toHaveAttribute('fill','#ddf0e3')
  await expect(exporterC).toHaveAttribute('opacity','0.25')
  await page.getByRole('heading',{name:'회원사 연결도',exact:true}).hover()
  await expect(producerNode.locator('rect')).toHaveAttribute('fill','#fff')
  await page.getByRole('button',{name:'생산조합 가와 수출회사 나 계약 2건',exact:true}).click()
  await expect(page.getByRole('region',{name:'선택한 계약 관계'})).toContainText('인도 후 30일 이내 지급')
  await page.getByRole('button',{name:'생산자회원사 생산조합 가 연결 강조',exact:true}).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button',{name:'생산자회원사 생산조합 가 연결 강조',exact:true})).toHaveAttribute('aria-pressed','true')
  await exporterB.hover()
  await expect(exporterC).toHaveAttribute('opacity','0.25')
  await page.getByRole('heading',{name:'회원사 연결도',exact:true}).hover()
  await expect(producerNode.locator('rect')).toHaveAttribute('fill','#b7ddc4')
  await expect(exporterC.locator('rect')).toHaveAttribute('fill','#ddf0e3')
  await page.getByRole('button',{name:'확대',exact:true}).click()
  await page.getByRole('button',{name:'화면 맞춤',exact:true}).click()
  await page.screenshot({path:'output/contracts-graph.png',fullPage:true})
  await page.getByRole('tab',{name:'계약 목록',exact:true}).click()
  await page.getByRole('row').filter({hasText:'사과 공급·수출 계약'}).getByRole('button',{name:'변경계약',exact:true}).click()
  await page.getByLabel('계약명',{exact:true}).fill('사과 공급 변경계약')
  await page.getByLabel('계약 PDF 업로드',{exact:true}).setInputFiles({name:'변경계약.pdf',mimeType:'application/pdf',buffer:pdf()})
  await expect(page.getByRole('img',{name:'계약 원본 1쪽',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'계약 저장',exact:true}).click()
  await expect(page.getByRole('heading',{name:/원계약 #1의 변경계약/})).toBeVisible()
  const sidebar=page.locator('.layout > aside'),layout=page.locator('.layout')
  expect((await sidebar.boundingBox())!.height).toBeGreaterThan(1000)
  expect(Math.abs((await sidebar.boundingBox())!.height-(await layout.boundingBox())!.height)).toBeLessThan(2)
  await page.getByRole('button',{name:'계약 저장',exact:true}).scrollIntoViewIfNeeded()
  const sidebarBottom=await sidebar.evaluate(el=>el.getBoundingClientRect().bottom)
  expect(sidebarBottom).toBeGreaterThanOrEqual(700)
  await expect(nav.getByRole('link',{name:'계약서 등록',exact:true})).toBeInViewport()
  await page.setViewportSize({width:800,height:900})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy()
  await page.screenshot({path:'output/contracts-pdf-review.png',fullPage:true})
  await page.getByRole('button',{name:'입력 닫기',exact:true}).click()
  await page.goto('/#/members')
  await page.getByRole('gridcell',{name:'생산조합 가',exact:true}).dblclick()
  await expect(page.getByRole('heading',{name:'회원사 정보 수정',exact:true})).toBeVisible()
  await page.getByRole('link',{name:'관련 계약·연결 회원사 조회',exact:true}).click()
  await expect(page).toHaveURL(/contracts\?member=1/)
  await expect(page.getByText('회원사별 조회:',{exact:false})).toBeVisible()
  expect(errors).toEqual([])
})
