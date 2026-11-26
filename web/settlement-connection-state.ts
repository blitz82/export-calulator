import {quantityUnits} from './sorting-quantity'

export type ConnectionState='complete'|'remaining'|'check'
export const connectionAppearance={
  complete:{color:'#2563eb',label:'매칭 완료'},
  remaining:{color:'#c56a09',label:'잔량 있음'},
  check:{color:'#dc2626',label:'점검 필요'},
} as const

// Remaining amounts include every connection, including those hidden by filters.
export function connectionState(left:string,right:string,leftRemaining:bigint,rightRemaining:bigint):ConnectionState {
  const a=quantityUnits(left.replaceAll(',','').trim()),b=quantityUnits(right.replaceAll(',','').trim())
  if(a===null||b===null||a<=0n||b<=0n||a!==b||leftRemaining<0n||rightRemaining<0n)return 'check'
  return leftRemaining===0n&&rightRemaining===0n?'complete':'remaining'
}
