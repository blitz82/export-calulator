import {quantityText} from './sorting-quantity'
import type {Ref,File} from './settlement-model'
export type Allocation={member_id:number;amount:string}
export type Portion=Allocation&{kg:string}
export type Selection={snapshot_id:number;portions:Portion[];attribution:Allocation[];reason:string}
export type Review={member_id:number;decision:string;date:string;reviewer:string;note:string;difference_reason:string;unpaid_reason:string;expected_date:string;evidence:Ref[]}
export type Resolution={key:string;number:string;date:string;total:string;scope_amount:string;allocations:Allocation[];evidence:Ref[]}
export type Payment={key:string;reference:string;date:string;recipient:string;recipient_reason:string;refund:boolean;amount:string;allocations:Allocation[];evidence:Ref[];import_source?:number;import_rows?:number[]}
export type ReportDraft={title:string;year:number;month:number;start:string;end:string;destination:string;writer:string;note:string;reason:string;selections:Selection[];reviews:Review[];resolutions:Resolution[];payments:Payment[];evidence:Ref[];declaration_evidence:(Ref&{declaration_id:number})[];reference_ack:boolean;personal_check:boolean;source_change_ack:boolean}
export type ReportRow={id:number;revision:number;state:string;previous:number|null;data:ReportDraft;package?:number|null}
export type Member={member_id:number;name:string;number:string;kg:string;amount:string|null;reported_amount?:string;reported_kg?:string}
export type ReportCatalog={files:File[];settlements:{snapshot_id:number;job_id:number;title:string;year:number;start:string;end:string;amount:string;members:Member[]}[]}
export type ReportPreview={revision:number;errors:string[];warnings:string[];totals:Record<string,string>;members:(Member&{decision:string|null;resolved:string;paid:string;refund:string;net:string;unpaid:string;status:string;review?:Review})[];selections:{snapshot_id:number;snapshot:{data:{title:string};result:{lines:{declaration_id:number;declaration_data:{number:string;line:string;hs:string;owner_name:string;agent_name:string};batch_title:string;left:string;right:string;producer:{name:string}}[];formula:string}}}[];manifest?:{name:string;start:number;end:number;sha:string;uses:{label:string;page:number}[]}[]}
export const reportStates:Record<string,string>={DRAFT:'작성 중',FROZEN:'제출본 보존',SUBMITTED:'제출 기록 있음',UNLOCKED:'잠금 해제·정정 필요',SUPERSEDED:'정정 전 보존본'}
export function newReport(today:string):ReportDraft{const y=Number(today.slice(0,4)),m=Number(today.slice(5,7));return {title:`${m}월 공동선별비 정산·지급 보고`,year:y,month:m,start:`${y}-${String(m).padStart(2,'0')}-01`,end:new Date(Date.UTC(y,m,0)).toISOString().slice(0,10),destination:'aT 관할 지역본부',writer:'',note:'',reason:'',selections:[],reviews:[],resolutions:[],payments:[],evidence:[],declaration_evidence:[],reference_ack:false,personal_check:false,source_change_ack:false}}
export function blankReview(id:number):Review{return {member_id:id,decision:'',date:'',reviewer:'',note:'',difference_reason:'',unpaid_reason:'',expected_date:'',evidence:[]}}
const wholeWon=(v:string)=>/^\d+(?:\.0+)?$/.test(v)?BigInt(v.split('.')[0]):null
const micro=(v:string)=>{const m=/^(\d+)(?:\.(\d{1,6}))?$/.exec(v);return m?BigInt(m[1])*1_000_000n+BigInt((m[2]??'').padEnd(6,'0')):null}
export function selectedMembers(draft:ReportDraft,catalog:ReportCatalog):Member[]{
  const map=new Map<number,Member>()
  for(const selection of draft.selections){
    const source=catalog.settlements.find(s=>s.snapshot_id===selection.snapshot_id)
    for(const member of source?.members??[]){
      const portion=selection.portions.find(p=>p.member_id===member.member_id)
      if(selection.portions.length&&!portion)continue
      const value=map.get(member.member_id)??{...member,amount:'0',kg:'0'},amount=portion?.amount??member.amount
      const before=value.amount===null?null:wholeWon(value.amount),next=amount===null?null:wholeWon(amount)
      value.amount=before===null||next===null?null:String(before+next)
      const a=micro(value.kg),b=micro(portion?.kg??member.kg);value.kg=a===null||b===null?'미확인':quantityText(a+b)
      map.set(member.member_id,value)
    }
  }
  return [...map.values()].sort((a,b)=>a.name.localeCompare(b.name,'ko'))
}

export function suggestSingleAllocation(members:{member_id:number}[],allocations:Allocation[],previous:string,next:string):Allocation[]{
  if(members.length!==1)return allocations
  if(!allocations.length||(allocations.length===1&&allocations[0].member_id===members[0].member_id&&allocations[0].amount===previous))return [{member_id:members[0].member_id,amount:next}]
  return allocations
}
