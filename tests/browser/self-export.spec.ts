import {test,expect} from '@playwright/test'
test.skip(process.env.EPMS_SELF_EXPORT_E2E!=='1','Requires a fresh isolated packaged database')
function pdf(){
  const stream='BT /F1 18 Tf 30 100 Td (SELF EXPORT SYNTHETIC EVIDENCE) Tj ET'
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Count 1 /Kids [3 0 R] >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`]
  let data='%PDF-1.4\n';const offsets:number[]=[];objects.forEach((o,i)=>{offsets.push(Buffer.byteLength(data));data+=`${i+1} 0 obj\n${o}\nendobj\n`});const xref=Buffer.byteLength(data)
  return Buffer.from(data+'xref\n0 6\n0000000000 65535 f \n'+offsets.map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
}
test('self exporter is saved and physically matched without any self agreement',async({page,request})=>{
  test.setTimeout(90000);page.setDefaultTimeout(15000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  const headers={'x-epms-token':(await(await request.get('/api/session')).json()).token}
  expect((await(await request.get('/api/office',{headers})).json()).item).toBeNull()
  expect((await request.post('/api/organizations?name=가상자기수출검증',{headers})).ok()).toBeTruthy()
  const members=[{name:'가상 직접수출조합',number:'1234567815',kind:'복수',approved:'2024-01-01'},{name:'가상 수출업체',number:'1234567829',kind:'수출업체',approved:'2024-01-01'}]
  for(const values of members)expect((await(await request.post('/api/org/1/data/members',{headers,data:[{values}]})).json())[0].row).toBeTruthy()
  const path='/api/org/1/settlements',doc=(await(await request.post(path+'/documents/upload?name=가상_지정증빙.pdf&kind=시설지정',{headers,data:pdf()})).json()).id
  expect((await request.post('/api/org/1/sorting/facilities',{headers,data:{name:'가상 자기수출 선별장',product:'가상자기수출검증',member_number:members[0].number,designation:'가상지정',valid_from:'2025-01-01',valid_to:'2027-12-31',evidence_id:doc}})).ok()).toBeTruthy()
  await page.route('**/api/org/1/settlements',async route=>{if(route.request().method()==='GET')await new Promise(resolve=>setTimeout(resolve,300));await route.continue()})
  const scenarios=[[0,0],[0,1],[1,0]]
  for(let i=0;i<scenarios.length;i++){
    const [owner,agent]=scenarios[i],line=String(i+1).padStart(3,'0'),title=`가상 자기수출 선별 ${i+1}`
    const values:Record<string,string>={number:'12345-26-123456',line,version:'1',declared:'2026-07-04',shipped:'2026-07-05',usd:'100',kg:'100',hs:'0808100000',scope:'대상',status:'유효'}
    for(const [role,m] of [['maker',members[0]],['owner',members[owner]],['agent',members[agent]]] as const){const n=m.number;values[role]=role==='maker'?n:`${n.slice(0,3)}-${n.slice(3,5)}-${n.slice(5)}`;values[role+'_name']=m.name;values[role+'_state']='회원 대조'}
    const saved=(await(await request.post('/api/org/1/data/lines',{headers,data:[{values}]})).json())[0];expect(saved.row).toBeTruthy();expect(saved.issues.filter((p:{severity:string})=>p.severity!=='INFO')).toEqual([])
    await page.goto(`/#/lines?row=${saved.row.id}`);await page.getByRole('button',{name:'저장',exact:true}).click();await expect(page.locator('.ledger-status [role=status]')).toContainText('저장됨');await expect(page.locator('.ledger-status [role=status]')).not.toContainText('보완')
    const batch=await(await request.post(path+'/sources',{headers,data:{facility_id:1,title,external_id:'self-'+i,period_type:'일별',start:`2026-07-0${i+1}`,end:`2026-07-0${i+1}`,producer_id:1,export_quantity:'100',evidence:[{file:doc,page:1}],confirmed:true}})).json();expect(batch.id).toBeTruthy()
    const catalog=await(await request.get(path+'/catalog',{headers})).json();expect(catalog.declarations.find((d:{id:number})=>d.id===saved.row.id).errors).toEqual([])
    const job=await(await request.post(path,{headers,data:{request_key:'self-job-'+i,title:'가상 자기수출 정산 '+(i+1),year:2026,facility_id:1,start:'2026-07-01',end:'2026-07-31',policy:{unit_price:'10',rate:'50',assumptions_confirmed:true},lines:[]}})).json()
    await page.goto(`/#/sorting?job=${job.id}`);await page.getByRole('button',{name:`선별 ${title} 연결`,exact:true}).click();await page.getByRole('button',{name:`신고 12345-26-123456 / ${line} 연결`,exact:true}).click()
    const confirm=page.getByRole('button',{name:'연결 확정·참고 계산 보존',exact:true});await expect(confirm).toBeEnabled();await confirm.click();await expect(page.getByRole('button',{name:'결과 PDF 다운로드',exact:true})).toBeVisible()
    const after=await(await request.get(path+'/catalog',{headers})).json();expect(after.batches.find((b:{id:number})=>b.id===batch.id).available).toBe('0');expect(after.declarations.find((d:{id:number})=>d.id===saved.row.id).available).toBe('0')
  }
  const ids:number[]=[]
  for(const year of [2025,2026]){expect((await request.put(`/api/org/1/policies/${year}`,{headers,data:{cutoff:`${year}-12-31`,basis:'shipped',hs:['0808100000'],reason:'가상 자기수출 검증'}})).ok()).toBeTruthy();ids.push((await(await request.post(`/api/org/1/runs?year=${year}`,{headers})).json()).id)}
  const result=await(await request.get(`/api/org/1/runs/${ids[1]}`,{headers})).json();expect(result.ready).toBe(true);const own=result.summary.find((m:{number:string})=>m.number===members[0].number);expect(Number(own.direct_usd)).toBe(300);expect(Number(own.indirect_usd)).toBe(0)
  const excel=await request.post(`/api/org/1/annual-submission?previous_id=${ids[0]}&run_id=${ids[1]}&final=true`,{headers});expect(excel.ok()).toBeTruthy()
  expect((await(await request.get('/api/org/1/contracts',{headers})).json()).length).toBe(0)
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/self-export-matching-verified.png',fullPage:true});expect(errors).toEqual([])
})
