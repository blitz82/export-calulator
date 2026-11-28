import {quantityUnits,quantityText} from './sorting-quantity'
import type {Ref} from './settlement-model'
export type IntakeDocument=Ref&{kind:string;relation:string;replaces_file:number|null;note:string}
export type ReceiptData={facility_id:number;title:string;period_type:string;start:string;end:string;received:string;kind:string;parent_id:number|null;documents:IntakeDocument[];needs_supplement:boolean;supplement_note:string;memo:string;reason:string}
export type Receipt={id:number;facility:number;revision:number;data:ReceiptData;state?:string;source_count?:number;cost_count?:number;export_quantity?:string|null;linked_quantity?:string;connection?:string;issues?:string[]}
export const belongs=(data:{receipt_id?:number|null;receipt_ids?:number[]},id:number)=>data.receipt_id===id||!!data.receipt_ids?.includes(id)
export function ocrMoneyInput(value:string):string|null{
  const match=value.trim().match(/^((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,6})?)\s*(원|KRW)?$/i)
  if(!match)return null
  const amount=quantityUnits(match[1].replaceAll(',',''));return amount===null?null:quantityText(amount)
}
export function ocrInput(field:string,value:string,unit:string):string|null{
  if(field==='start'){
    const match=value.trim().match(/^(\d{4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})일?$/)
    if(!match)return null
    const day=`${match[1]}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`,date=new Date(day+'T00:00:00Z')
    return !Number.isNaN(date.getTime())&&date.toISOString().slice(0,10)===day?day:null
  }
  if(!['export_quantity','total','domestic','rejected','loss'].includes(field))return null
  const match=value.trim().match(/^((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,6})?)\s*(kg|㎏|킬로그램|t|톤)?$/i)
  if(!match)return null
  let amount=quantityUnits(match[1].replaceAll(',',''));if(amount===null)return null
  const sourceTon=match[2]?/^(t|톤)$/i.test(match[2]):unit==='t'
  if(sourceTon&&unit!=='t')amount*=1000n
  if(!sourceTon&&unit==='t'){if(amount%1000n)return null;amount/=1000n}
  const result=quantityText(amount);return quantityUnits(result)===null?null:result
}

export function intakeErrors(d:{start:string;end:string;period_type:string;producer_id:number|null;export_quantity:string;evidence:{file?:number;page?:number}[];change_reason:string},existing=false){
  const errors:Record<string,string>={}
  if(!d.start)errors.start='선별일을 입력해 주세요.'
  if(d.period_type==='월별'&&!d.end)errors.end='선별 종료일을 입력해 주세요.'
  else if(d.end&&d.end<d.start)errors.end='선별 종료일은 시작일 이후여야 합니다.'
  if(!d.producer_id)errors.producer_id='수출신고필증의 제조자와 매칭할 생산자회원사를 선택해 주세요.'
  if(d.export_quantity==='')errors.export_quantity='수출용 선별량을 입력해 주세요. 미확인과 0은 구분됩니다.'
  else if(quantityUnits(d.export_quantity)===null)errors.export_quantity='수출용 선별량의 숫자 형식과 소수 자릿수를 확인해 주세요.'
  if(!d.evidence.length||d.evidence.some(r=>!r.file))errors.evidence='확인한 PDF를 선택해 주세요.'
  if(existing&&!d.change_reason.trim())errors.change_reason='수정 이유를 입력해 주세요.'
  return errors
}
