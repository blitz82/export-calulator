import {test,expect} from '@playwright/test'
test.skip(process.env.EPMS_UX_E2E!=='1','Requires the isolated ux-v203-demo database')
test.describe.configure({mode:'serial'})
test.beforeEach(async({page})=>{page.on('dialog',d=>d.accept());await page.addInitScript(()=>localStorage.setItem('epms-work-month-1','2026-07'))})
test('protects facility and contract drafts, including save-and-move',async({page,request})=>{
 const headers={'x-epms-token':(await(await request.get('/api/session')).json()).token}
 await page.goto('/#/facilities');await page.getByRole('button',{name:'선별장 신규 등록',exact:true}).click()
 await page.locator('[name="name"]').fill('입력 보존 시험');await page.getByRole('button',{name:'목록으로',exact:true}).click()
 await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'계속 작성',exact:true}).click();await expect(page.locator('[name="name"]')).toHaveValue('입력 보존 시험')
 await page.locator('[name="designation"]').fill('UX-'+Date.now());await page.locator('[name="valid_from"]').fill('2026-01-01');await page.locator('[name="member_number"]').selectOption('1234567815');await page.locator('[name="evidence_id"]').selectOption({index:1})
 await page.getByRole('navigation').getByRole('link',{name:'수출신고필증 등록',exact:true}).click();await page.getByRole('button',{name:'저장하고 이동',exact:true}).click();await expect(page).toHaveURL(/#\/lines$/)
 const rows=await(await request.get('/api/org/1/sorting/facilities',{headers})).json();expect(rows.some((r:{data:{name:string}})=>r.data.name==='입력 보존 시험')).toBe(true)
 await page.goto('/#/contracts');await page.getByRole('button',{name:'계약 신규 등록',exact:true}).click();await page.getByLabel('계약명',{exact:true}).fill('보존할 계약')
 await page.getByRole('navigation').getByRole('link',{name:'수출신고필증 등록',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'계속 작성',exact:true}).click();await expect(page.getByLabel('계약명',{exact:true})).toHaveValue('보존할 계약')
 await page.evaluate(()=>history.back());await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'계속 작성',exact:true}).click();await expect(page.getByLabel('계약명',{exact:true})).toHaveValue('보존할 계약')
 await page.screenshot({path:'output/ux-v203/contract-draft.png',fullPage:true})
 await page.getByRole('navigation').getByRole('link',{name:'수출신고필증 등록',exact:true}).click();await page.getByRole('button',{name:'버리고 이동',exact:true}).click();await expect(page).toHaveURL(/#\/lines$/)
})
test('restores graph matching and compact controls while preserving the calculation step',async({page})=>{
 await page.setViewportSize({width:1366,height:900});await page.goto('/#/sorting?job=1')
 await expect(page.getByRole('group',{name:'선별물량 연결 작업판',exact:true})).toBeVisible();await expect(page.locator('.matching-lists')).toHaveCount(0)
 const source=page.getByRole('button',{name:'선별 7월 선별 → 8월 선적 연결',exact:true});await source.click()
 await expect(page.locator('.settlement-canvas').getByText(/2026-08-03/)).toBeVisible()
 await expect(page.getByRole('button',{name:'축소',exact:true})).toBeVisible();await page.getByRole('button',{name:'축소',exact:true}).click();await expect(page.locator('.board-zoom')).toContainText('80%')
 await page.getByRole('button',{name:'화면 맞춤',exact:true}).click();await expect(page.locator('.board-zoom')).toContainText('100%')
 expect(await page.getByRole('button',{name:'정산 작업 만들기',exact:true}).evaluate(e=>parseFloat(getComputedStyle(e).fontSize))).toBe(12)
 await page.locator('.workflow-steps').getByRole('link',{name:'4 지원액 확인',exact:true}).click();await expect(page).toHaveURL(/stage=calculate.*job=1|job=1.*stage=calculate/)
 await expect(page.locator('.board-policy-settings')).toBeVisible();await page.reload();await expect(page.getByLabel('저장한 정산 작업',{exact:true})).toHaveValue('1');await expect(page.locator('.board-policy-settings')).toBeVisible()
 await page.screenshot({path:'output/ux-v204-graph.png',fullPage:true})
})

test('keeps malformed numeric paste intact and preserves input after server failure',async({page})=>{
 await page.goto('/#/sorting-records?receipt=1');await page.getByRole('button',{name:'2 내용 입력하기',exact:true}).click();await page.getByRole('button',{name:'선별기록 추가',exact:true}).click()
 const weight=page.getByLabel('2행 수출용 물량',{exact:true});await weight.fill('1.234,56');await expect(weight).toHaveValue('1.234,56');await expect(weight).toHaveAttribute('aria-invalid','true');await expect(page.getByRole('alert').filter({hasText:'숫자 형식을 확인하세요.'})).toBeVisible()
 await weight.fill('12345.6700');await expect(weight).toHaveValue('12,345.6700');await expect(weight).toHaveAttribute('aria-invalid','false')
 await page.route('**/api/org/*/settlements/sources',r=>r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'시험 충돌: 입력을 유지하세요.'})}),{times:1})
 await page.getByRole('button',{name:'PDF 확인·기록 저장',exact:true}).last().click();await expect(page.getByRole('alert').filter({hasText:'시험 충돌'})).toBeVisible();await expect(weight).toHaveValue('12,345.6700')
 await page.locator('.novice-record').last().screenshot({path:'output/ux-v203/input-recovery.png'})
})
test('shows actionable missing writer errors and offers safe single-member allocations',async({page,request})=>{
 await page.goto('/#/monthly-reports');await page.getByRole('button',{name:'보고서 저장·점검',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'보고 작성자를 입력해 주세요.'}).first()).toBeVisible();await expect(page.getByLabel('보고 작성자',{exact:true})).toBeFocused()
 const headers={'x-epms-token':(await(await request.get('/api/session')).json()).token};const original=(await(await request.get('/api/org/1/monthly-reports',{headers})).json()).find((r:{id:number})=>r.id===1);const response=await request.post('/api/org/1/monthly-reports',{headers,data:{...original.data,reason:'격리 자동배분 검증'}});expect(response.ok()).toBe(true);const created=await response.json();
 await page.goto('/#/monthly-reports?report='+created.id);await page.getByRole('tab',{name:'회원사별 검토·지급',exact:true}).click();await page.getByLabel('결재문서 총액',{exact:true}).fill('6000');await expect(page.getByLabel('이번 사업 대상 결의액',{exact:true})).toHaveValue('6,000');await expect(page.getByLabel('회원사 배분액',{exact:true}).first()).toHaveValue('6,000')
 await page.getByLabel('회원사 배분액',{exact:true}).first().fill('4000');await page.getByLabel('결재문서 총액',{exact:true}).fill('7000');await expect(page.getByLabel('회원사 배분액',{exact:true}).first()).toHaveValue('4,000')
 await page.getByLabel('거래 전체 금액',{exact:true}).fill('8000');await expect(page.getByLabel('회원사 배분액',{exact:true}).last()).toHaveValue('8,000')
})
test('requires reviewed contracts and downloads a descriptive frozen report name',async({page,request})=>{
 const headers={'x-epms-token':(await(await request.get('/api/session')).json()).token}
 const original=(await(await request.get('/api/org/1/contracts',{headers})).json())[0]
 const payload={title:original.data.title,kind:original.data.kind,producer_id:original.producer_id,exporter_id:original.exporter_id,document_id:original.document_id,signed:original.data.signed,start:original.data.start,end:original.data.end,review:'미검토',evidence_pages:{parties:1},revision:original.revision,reason:'격리 UX 검증'}
 let response=await request.put('/api/org/1/contracts/'+original.id,{headers,data:payload});expect(response.ok()).toBe(true)
 let checks=await(await request.get('/api/org/1/line-checks',{headers})).json();expect(checks['1'].some((p:{code:string})=>p.code==='D090')).toBe(true)
 payload.review='확인 완료';payload.revision=(await response.json()).revision;response=await request.put('/api/org/1/contracts/'+original.id,{headers,data:payload});expect(response.ok()).toBe(true)
 checks=await(await request.get('/api/org/1/line-checks',{headers})).json();expect((checks['1']??[]).some((p:{code:string})=>p.code==='D090')).toBe(false)
 await page.goto('/#/monthly-reports?report=1');await page.getByRole('tab',{name:'통합 PDF·제출 이력',exact:true}).click()
 if(await page.getByRole('button',{name:'제출본 생성·보존',exact:true}).count())await page.getByRole('button',{name:'제출본 생성·보존',exact:true}).click()
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'통합 PDF 다운로드',exact:true}).click();const file=await download;expect(file.suggestedFilename()).toMatch(/^2026-07_사과_공동선별비_제출본_1차_보고1\.pdf$/);await file.saveAs('output/ux-v203/'+file.suggestedFilename())
 await page.screenshot({path:'output/ux-v203/report-frozen.png',fullPage:true})
})
