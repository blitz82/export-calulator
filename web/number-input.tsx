import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react'
import type {InputHTMLAttributes} from 'react'
export function normalizeNumeric(value:string):string|null{
  value=value.trim()
  if(!/^-?(?:\d*|\d{1,3}(?:,\d{3})+)(?:\.\d*)?$/.test(value))return null
  return value.replaceAll(',','')
}
export function formatNumeric(value:string){
  const raw=normalizeNumeric(value)
  if(raw===null)return value
  const [whole,fraction]=raw.split('.')
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction===undefined?'':'.'+fraction)
}
const invalidMessage='숫자 형식을 확인하세요. 예: 1,234.56 (쉼표는 천 단위, 점은 소수점)'
export function NumberInput({value,onChange,onBlur,onPaste,...props}:InputHTMLAttributes<HTMLInputElement>){
  const ref=useRef<HTMLInputElement>(null),position=useRef<number|null>(null),pasting=useRef(false),id=useId(),[error,setError]=useState('')
  const display=formatNumeric(String(value??''))
  useEffect(()=>{if(normalizeNumeric(String(value??''))!==null){setError('');ref.current?.setCustomValidity('')}},[value])
  useLayoutEffect(()=>{if(position.current!==null&&ref.current){ref.current.setSelectionRange(position.current,position.current);position.current=null}})
  return <span className="numeric-input-control"><input {...props} ref={ref} type="text" inputMode="decimal" value={display} aria-invalid={!!error||props['aria-invalid']} aria-describedby={[props['aria-describedby'],error?id:undefined].filter(Boolean).join(' ')||undefined} onPaste={e=>{pasting.current=true;onPaste?.(e)}} onBlur={e=>{if(normalizeNumeric(e.currentTarget.value)===null){setError(invalidMessage);e.currentTarget.setCustomValidity(invalidMessage)}onBlur?.(e)}} onChange={e=>{
    const input=e.currentTarget,event=e.nativeEvent as InputEvent,bulk=pasting.current||event.inputType==='insertFromPaste'||(event.data?.length??0)>1
    pasting.current=false
    const raw=(bulk?input.value.trim():input.value).replaceAll(',',''),before=input.value.slice(0,input.selectionStart??0).replaceAll(',','').length
    if(!/^-?\d*(?:\.\d*)?$/.test(raw)||(bulk&&normalizeNumeric(input.value)===null)){input.setCustomValidity(invalidMessage);setError(invalidMessage);position.current=null;onChange?.(e);return}
    input.setCustomValidity('');setError('')
    const formatted=formatNumeric(raw);let count=0,caret=0
    while(caret<formatted.length&&count<before){if(formatted[caret]!==',')count++;caret++}
    position.current=caret
    onChange?.({...e,target:{...e.target,value:raw},currentTarget:{...e.currentTarget,value:raw}})
  }}/>{error&&<span id={id} className="field-error" role="alert">{error}</span>}</span>
}
