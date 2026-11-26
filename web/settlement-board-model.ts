import type {Batch,Declaration,Edge} from './settlement-model'
import {quantityUnits,quantityText} from './sorting-quantity'
import {connectionState} from './settlement-connection-state'
export const amountUnits=(v:string)=>quantityUnits(v.replaceAll(',','').trim())??0n
export const weightText=(v:bigint)=>v<0n?'-'+quantityText(-v):quantityText(v)
export const businessKey=(v:string)=>v.replace(/\D/g,'')
// Card positions depend only on record dates and stable identifiers, never links or balances.
const dateKey=(v:string|undefined)=>v||'9999-12-31'
export const compareBatchDates=(a:Batch,b:Batch)=>dateKey(a.data.start||a.data.work_date).localeCompare(dateKey(b.data.start||b.data.work_date))||a.id-b.id
export const compareDeclarationDates=(a:Declaration,b:Declaration)=>dateKey(a.shipped||a.declared).localeCompare(dateKey(b.shipped||b.declared))||a.number.localeCompare(b.number,undefined,{numeric:true})||a.line.localeCompare(b.line,undefined,{numeric:true})||a.id-b.id
export function sameProducer(b:Batch,d:Declaration){const key=businessKey(b.data.producer?.number??'');return key.length===10&&key===businessKey(d.maker)}
export function boardBalances(batches:Batch[],declarations:Declaration[],lines:Edge[]){
  const left=new Map(batches.map(b=>[b.id,amountUnits(b.available)])),right=new Map(declarations.map(d=>[d.id,amountUnits(d.available)])),leftEdges=new Map<number,number[]>(),rightEdges=new Map<number,number[]>()
  const poolRemaining=new Map<number,bigint>(),batchIndex=new Map(batches.map(b=>[b.id,b]))
  for(const b of batches)poolRemaining.set(b.pool_id??b.id,amountUnits(b.pool_available??b.available))
  lines.forEach((e,i)=>{left.set(e.batch_id,(left.get(e.batch_id)??0n)-amountUnits(e.left));right.set(e.declaration_id,(right.get(e.declaration_id)??0n)-amountUnits(e.right));leftEdges.set(e.batch_id,[...(leftEdges.get(e.batch_id)??[]),i]);rightEdges.set(e.declaration_id,[...(rightEdges.get(e.declaration_id)??[]),i])})
  for(const e of lines){const b=batchIndex.get(e.batch_id);if(b){const id=b.pool_id??b.id;poolRemaining.set(id,(poolRemaining.get(id)??0n)-amountUnits(e.left))}}
  for(const b of batches){const remaining=poolRemaining.get(b.pool_id??b.id)??0n;if(remaining<(left.get(b.id)??0n))left.set(b.id,remaining)}
  return {left,right,leftEdges,rightEdges,poolRemaining,states:lines.map(e=>connectionState(e.left,e.right,left.get(e.batch_id)??-1n,right.get(e.declaration_id)??-1n))}
}
export function sourceWeightTotal(batches:Batch[],balance?:ReturnType<typeof boardBalances>):bigint{
  const groups=new Map<number,{sum:bigint;cap:bigint}>()
  for(const b of batches){const id=b.pool_id??b.id,current=groups.get(id)??{sum:0n,cap:balance?.poolRemaining.get(id)??amountUnits(b.pool_available??b.available)};current.sum+=balance?.left.get(b.id)??amountUnits(b.available);groups.set(id,current)}
  return [...groups.values()].reduce((sum,g)=>sum+(g.cap<0n?g.cap:g.sum<g.cap?g.sum:g.cap),0n)
}
export function candidateInfo(b:Batch|undefined,d:Declaration,remaining:bigint,start:string,end:string){
  const day=d.shipped||d.declared||'',producer=!!b&&sameProducer(b,d),period=!!day&&(!start||day>=start)&&(!end||day<=end),sourceDay=b?.data.start||b?.data.work_date
  return {recommended:producer&&period&&remaining>0n&&!d.errors.length,reasons:[producer?'동일 생산자':'생산자 확인',period?'대상 기간':'기간 확인',remaining>0n?'잔량 있음':'잔량 없음',d.errors.length?'검증 확인 필요':'등록·계약 검증 통과'],distance:sourceDay&&day?Math.abs(Date.parse(day)-Date.parse(sourceDay)):Infinity}
}
export function proposeConnections(batches:Batch[],declarations:Declaration[],lines:Edge[],ids:number[],start:string,end:string){
  const state=boardBalances(batches,declarations,lines),proposed:Edge[]=[]
  const sources=batches.filter(b=>ids.includes(b.id)&&b.ready!==false&&(!start||(b.data.start??b.data.work_date??'')>=start)&&(!end||(b.data.end??b.data.work_date??'')<=end)).sort((a,b)=>(a.data.start??'').localeCompare(b.data.start??'')||a.id-b.id)
  for(const b of sources){
    const pool=b.pool_id??b.id
    const candidates=declarations.filter(d=>candidateInfo(b,d,state.right.get(d.id)??0n,start,end).recommended).sort((a,c)=>candidateInfo(b,a,1n,start,end).distance-candidateInfo(b,c,1n,start,end).distance||a.number.localeCompare(c.number)||a.line.localeCompare(c.line)||a.id-c.id)
    for(const d of candidates){
      if(lines.length+proposed.length>=1000)return proposed
      if(lines.some(e=>e.batch_id===b.id&&e.declaration_id===d.id))continue
      const a=state.left.get(b.id)??0n,p=state.poolRemaining.get(pool)??0n,r=state.right.get(d.id)??0n,q=[a,p,r].reduce((x,y)=>x<y?x:y)
      if(q<=0n)continue
      proposed.push({batch_id:b.id,batch_revision:b.revision,declaration_id:d.id,declaration_revision:d.revision,left:quantityText(q),right:quantityText(q),reason:'',exception_reason:'',period_reason:'',evidence:[]})
      state.left.set(b.id,a-q);state.poolRemaining.set(pool,p-q);state.right.set(d.id,r-q)
    }
  }
  return proposed
}
