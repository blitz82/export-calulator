// Actual packaged UI, using only the isolated synthetic screenshot database.
import fs from 'node:fs/promises'
import path from 'node:path'
import {chromium,request} from 'playwright'

const base='http://127.0.0.1:18796',out=path.resolve('output/readiness-hq-captures')
await fs.mkdir(out,{recursive:true})
const api=await request.newContext({baseURL:base})
const token=(await(await api.get('/api/session')).json()).token,headers={'x-epms-token':token}
async function call(method,url,data){
  const result=await api.fetch(url,{method,headers,...(data===undefined?{}:{data})})
  if(!result.ok())throw new Error(`${method} ${url}: ${result.status()} ${await result.text()}`)
  return result.json()
}
const org=(await call('GET','/api/office')).item
if(!org||!(/가상|합성/).test(org.name))throw new Error('Expected the isolated synthetic test organization.')
const prefix=`/api/org/${org.id}/sorting`
const claims=await call('GET',prefix+'/claims')
let selected
for(const claim of claims){
  const listing=await call('GET',`${prefix}/claims/${claim.id}/payment-preparations`)
  if(listing.plans.length&&listing.statements.length){selected={claim,listing};break}
}
if(!selected)throw new Error('Synthetic preparation scenario missing.')
const {claim,listing}=selected
const source=listing.plans.at(-1).statement_id
let plan=listing.plans.find(row=>row.state!=='CANCELLED')
if(!plan){
  const created=await call('POST',`${prefix}/claims/${claim.id}/payment-preparations`,{claim_revision:listing.claim_revision,statement_id:source,reason:'고화질 화면 예시용 합성 준비',request_key:crypto.randomUUID()})
  const initial=await call('GET',`${prefix}/payment-preparations/${created.id}`)
  await call('POST',`${prefix}/payment-preparations/${created.id}/revise`,{revision:initial.revision,state:'INPUT_REVIEWED',reason:'화면 설명용 입력 검토. 실제 지급 아님.',request_key:crypto.randomUUID(),members:initial.members.map(row=>({member_id:row.member_id,prepared_amount:'50',planned_date:'2027-04-01',documents_reviewed:true,basis:'화면 설명용 합성 수동 준비액'}))})
  plan={id:created.id,statement_id:source}
}
const browser=await chromium.launch({channel:'msedge',headless:true})
const context=await browser.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:2})
const page=await context.newPage(),errors=[]
page.on('pageerror',error=>errors.push(error.message))
const captures=[]
async function capture(file,title,target){
  const location=path.join(out,file)
  if(target)await target.screenshot({path:location,animations:'disabled'})
  else await page.screenshot({path:location,fullPage:true,animations:'disabled'})
  const bytes=await fs.readFile(location)
  captures.push({file,title,width:bytes.readUInt32BE(16),height:bytes.readUInt32BE(20),bytes:bytes.length})
}
try{
  await page.goto(base+'/#/sorting')
  await page.getByRole('combobox',{name:'검토할 신청',exact:true}).selectOption(String(claim.id))
  await page.getByRole('button',{name:'검토자료 보관',exact:true}).click()
  await page.getByRole('button',{name:'지급 준비 종합 점검',exact:true}).click()
  await page.getByRole('combobox',{name:'대조할 지급 준비 대장',exact:true}).selectOption(String(plan.id))
  await page.getByRole('heading',{name:/정책 판단 보류/}).waitFor()
  await page.getByRole('button',{name:'종합 점검 다시 조회',exact:true}).waitFor({state:'visible'})
  await page.waitForFunction(()=>document.querySelectorAll('.readiness-category').length===4)
  await page.evaluate(()=>document.fonts.ready)
  const panel=page.locator('section.panel').filter({has:page.getByRole('heading',{name:'지급 준비 종합 점검',exact:true})})
  await capture('01-full-screen-4k.png','전체 화면 · 4K 원본')
  await capture('02-readiness-panel.png','종합 점검 전체 내용',panel)
  const categories=page.locator('.readiness-category')
  for(const [index,name] of ['자료 보완','원본 재검토','정책 판단 보류','운영 조건 미충족'].entries())await capture(`0${index+3}-category-${index+1}.png`,name, categories.nth(index))
  const info=await page.getByRole('combobox',{name:'대조할 지급 준비 대장',exact:true}).inputValue()
  if(info!==String(plan.id)||errors.length)throw new Error('Capture verification failed: '+errors.join(', '))
  const manifest={source:'Packaged Lite UI, isolated synthetic database',viewport:{width:1920,height:1080},deviceScaleFactor:2,claim_id:claim.id,statement_id:plan.statement_id,preparation_id:plan.id,captures}
  await fs.writeFile(path.join(out,'manifest.json'),JSON.stringify(manifest,null,2),'utf8')
  const cards=captures.map(c=>`<article><h2>${c.title}</h2><p>${c.width} × ${c.height}px · 원본을 클릭해 확대하세요.</p><a href="${c.file}" target="_blank"><img src="${c.file}" alt="${c.title}" loading="lazy"></a><p><a href="${c.file}" download>PNG 원본 저장</a></p></article>`).join('')
  await fs.writeFile(path.join(out,'index.html'),`<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EPMS 종합 점검 고화질 캡처</title><style>body{font:16px/1.6 'Malgun Gothic',sans-serif;max-width:1500px;margin:auto;padding:24px;color:#202820;background:#f6f8f6}article{margin:32px 0;padding:18px;background:white;border:1px solid #bcc8bc}img{display:block;max-width:100%;height:auto;border:1px solid #ddd}a{color:#174e39}h1{font-size:24px}h2{font-size:20px}</style><h1>지급 준비 종합 점검 · 고화질 캡처</h1><p>실제 Lite 실행파일 화면입니다. 독립된 합성 시험 DB만 사용했습니다. 실제 수출·회원 지급 자료가 아니며 승인·지급 확정 화면도 아닙니다.</p><p>1920px 화면을 배율 2로 캡처했습니다. 아래 이미지는 화면 너비에 맞춰 표시되며 클릭하면 고해상도 PNG 원본을 볼 수 있습니다. 브라우저의 실제 크기/확대로 글자를 확인하세요.</p><nav>${captures.map(c=>`<a href="${c.file}" target="_blank">${c.title}</a>`).join(' · ')}</nav>${cards}</html>`,'utf8')
  await fs.writeFile(path.join(out,'읽어주세요.txt'),'index.html을 열면 전체 화면과 4개 분류별 캡처를 볼 수 있습니다. 이미지를 클릭해 원본 크기로 확대하세요. 실제 실행파일 화면이며 모든 데이터는 합성 예시입니다. 01은 전체 화면, 02는 종합 점검 본문 전체, 03~06은 분류별 확대 원본입니다.','utf8')
  process.stdout.write(JSON.stringify(captures,null,2))
}finally{await browser.close();await api.dispose()}
