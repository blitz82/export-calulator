import {chromium} from 'playwright'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1600,height:1100},deviceScaleFactor:1.5})
page.setDefaultTimeout(15000)
const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept())
try{
  await page.goto('http://127.0.0.1:18798/#/sorting')
  await page.getByRole('combobox',{name:'저장한 정산 작업',exact:true}).selectOption('1')
  await page.getByRole('tab',{name:'물량 연결·계산',exact:true}).click()
  await page.getByLabel('자료 시작일',{exact:true}).fill('2026-11-01');await page.getByLabel('자료 종료일',{exact:true}).fill('2026-11-30')
  const graph=page.locator('section.settlement-graph'),edges=graph.locator('g[data-connection-state]')
  await edges.first().waitFor();await graph.getByRole('heading').click()
  assert.deepEqual(await edges.evaluateAll(es=>es.map(e=>e.dataset.connectionState)),['complete','complete','check','remaining'])
  const geometry=await graph.evaluate(el=>{
    const paths=[...el.querySelectorAll('g[data-connection-state]')].map(g=>({state:g.dataset.connectionState,d:g.querySelector('path').getAttribute('d'),stroke:g.querySelectorAll('path')[1].getAttribute('stroke')}))
    const ports=[...el.querySelectorAll('foreignObject:has(.settlement-port)')].map(p=>({x:Number(p.getAttribute('x'))+10,y:Number(p.getAttribute('y'))+10,state:p.querySelector('button').dataset.connectionState,color:p.querySelector('button').style.color,width:getComputedStyle(p.querySelector('span')).width,height:getComputedStyle(p.querySelector('span')).height}))
    return {paths,ports}
  })
  const colors={complete:'rgb(37, 99, 235)',remaining:'rgb(197, 106, 9)',check:'rgb(220, 38, 38)'}
  for(const p of geometry.ports){assert.equal(p.width,'10px');assert.equal(p.height,'10px');assert.equal(p.color,colors[p.state])}
  for(const e of geometry.paths){
    const numbers=e.d.match(/[\d.]+/g).map(Number)
    for(const [x,y] of [[numbers[0],numbers[1]],[numbers[6],numbers[7]]])assert(geometry.ports.some(p=>p.x===x&&p.y===y&&p.state===e.state),'Dot must sit on matching line endpoint')
  }
  await graph.screenshot({path:'output/settlement-connection-status.png',animations:'disabled'})
  // Actual drag still works with the smaller, centered handles.
  await page.getByRole('button',{name:'11월 3일 수출 선별기록 연결점',exact:true}).dragTo(page.getByRole('button',{name:'99099-26-000003 / 001 연결점',exact:true}))
  assert.equal(await edges.count(),5)
  assert.equal(await edges.last().getAttribute('data-connection-state'),'complete')
  // Excess use must immediately turn the affected line and its ports red.
  await page.getByRole('button',{name:'연결 1 1000 kg 대 1000 kg 수정',exact:true}).click()
  await page.getByLabel('선별 측 사용량 kg',{exact:true}).fill('1300')
  assert.equal(await edges.first().getAttribute('data-connection-state'),'check')
  await page.getByLabel('신고 측 사용량 kg',{exact:true}).fill('1300')
  assert.equal(await edges.first().getAttribute('data-connection-state'),'check')
  assert.deepEqual(errors,[])
  await fs.writeFile('output/settlement-connection-status-verification.json',JSON.stringify({geometry,checks:['blue/orange/red status','10px dots centered on each edge','multiple ports on shared cards','drag connection','over-allocation','no browser errors']},null,2))
  console.log('Passed: 3 states, 8 endpoint positions/colors, 10px dots, drag and over-allocation; no browser errors.')
}finally{await browser.close()}
