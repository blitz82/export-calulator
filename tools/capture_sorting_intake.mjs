import {chromium,request} from 'playwright'
import fs from 'node:fs/promises'
const base='http://127.0.0.1:18802',api=await request.newContext({baseURL:base})
const token=(await(await api.get('/api/session')).json()).token,headers={'x-epms-token':token}
const receipts=await(await api.get('/api/org/1/sorting-receipts',{headers})).json(),receipt=receipts.find(r=>r.source_count===2&&r.connection==='일부 연결')
if(!receipt)throw new Error('Expected the isolated synthetic intake scenario')
const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1800,height:1150},deviceScaleFactor:1.5})
const errors=[];page.on('pageerror',e=>errors.push(e.message))
try{
  await page.goto(base+'/#/sorting-records?receipt='+receipt.id)
  await page.getByRole('tab',{name:'선별기록',exact:true}).waitFor()
  await page.getByLabel('자료 검색',{exact:true}).fill(receipt.data.title)
  await page.evaluate(()=>document.fonts.ready)
  await page.screenshot({path:'output/intake-menu-final.png',animations:'disabled'})
  await page.getByRole('button',{name:'1행 선택',exact:true}).click()
  await page.locator('.intake-rows').screenshot({path:'output/intake-rows-final.png',animations:'disabled'})
  await page.getByRole('button',{name:'원문',exact:true}).first().click();await page.getByRole('img',{name:'선별 증빙 1쪽',exact:true}).waitFor()
  await page.locator('.intake-two-pane').evaluate(el=>window.scrollTo(0,window.scrollY+el.getBoundingClientRect().top-12))
  await page.screenshot({path:'output/intake-pdf-final.png',animations:'disabled'})
  await page.getByRole('button',{name:'닫기',exact:true}).click();await page.getByRole('tab',{name:'첨부자료·보완사항',exact:true}).click()
  await page.getByText('접수·기록 변경 이력',{exact:true}).scrollIntoViewIfNeeded()
  await page.screenshot({path:'output/intake-history-final.png',animations:'disabled'})
  for(const width of [1100,820,500]){await page.setViewportSize({width,height:950});if(!await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth))throw new Error('Page overflow at '+width)}
  if(errors.length)throw new Error(errors.join('\n'))
  await fs.writeFile('output/intake-ui-verification.json',JSON.stringify({captured:4,viewport:[1800,1150],responsive:[1100,820,500],browser_errors:errors},null,2))
  console.log('Captured intake menu, rows, PDF comparison and history; three responsive widths passed.')
}finally{await browser.close();await api.dispose()}
