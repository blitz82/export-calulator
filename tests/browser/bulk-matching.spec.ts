import {test,expect} from '@playwright/test'
test.skip(process.env.EPMS_WORKFLOW_E2E!=='1','Requires isolated workflow fixture with members and contract')
test('multiple source proposals remain drafts and respect both sides',async({page,request})=>{
  test.setTimeout(60000);page.setDefaultTimeout(15000)
  const headers={'x-epms-token':(await(await request.get('/api/session')).json()).token}
  const path='/api/org/1/settlements',sources:number[]=[]
  for(const [day,kg] of [['04','100'],['05','200']]){
    const r=await request.post(path+'/sources',{headers,data:{facility_id:1,title:`가상 일괄 ${day}일`,external_id:`워크플로우-일괄-${day}`,period_type:'일별',start:`2026-07-${day}`,end:`2026-07-${day}`,producer_id:1,export_quantity:kg,evidence:[{file:1,page:1}],confirmed:true,overlap_reason:'격리된 별개 작업 물량 검증'}});expect(r.ok()).toBeTruthy();sources.push((await r.json()).id)
  }
  const r=await request.post('/api/org/1/data/lines',{headers,data:[{values:{number:'12345-26-123457',line:'001',version:'1',declared:'2026-07-05',shipped:'2026-07-05',usd:'100',kg:'300',hs:'0808100000',scope:'대상',status:'유효',maker:'1234567815',maker_name:'생산조합',maker_state:'회원 대조',owner:'1234567829',owner_name:'수출회사',owner_state:'회원 대조',agent:'1234567829',agent_name:'수출회사',agent_state:'회원 대조'}}]});expect(r.ok()).toBeTruthy()
  const created=await request.post(path,{headers,data:{request_key:'bulk-'+Date.now(),title:'가상 일괄 연결 검증',year:2026,facility_id:1,start:'2026-07-01',end:'2026-07-31',policy:{unit_price:'10',rate:'50',assumptions_confirmed:true},lines:[]}});expect(created.ok()).toBeTruthy();const job=await created.json()
  await page.goto(`/#/sorting?job=${job.id}`)
  const bulk=page.locator('details.board-bulk');await bulk.locator('summary').click();await bulk.getByRole('button',{name:'현재 보이는 기록 선택',exact:true}).click();await bulk.getByRole('button',{name:'잔량으로 연결 후보 만들기',exact:true}).click()
  await expect(bulk.locator('tbody tr')).toHaveCount(2);await bulk.getByRole('button',{name:'검토한 연결 반영',exact:true}).click()
  await expect(page.locator('.board-link-table summary')).toContainText('2건')
  await page.locator('details.board-job-settings summary').click();await page.locator('details.board-job-settings').getByRole('button',{name:'초안 저장',exact:true}).click()
  await expect(page.getByRole('status').filter({hasText:'초안을 저장했습니다.'})).toBeVisible()
  const jobs=await(await request.get(path,{headers})).json();expect(jobs.find((j:{id:number})=>j.id===job.id).data.lines.map((e:{left:string})=>e.left)).toEqual(['100','200'])
  const catalog=await(await request.get(path+'/catalog',{headers})).json();expect(catalog.batches.filter((b:{id:number})=>sources.includes(b.id)).map((b:{available:string})=>b.available)).toEqual(['100','200'])
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/workflow-bulk-matching.png',fullPage:true})
})
