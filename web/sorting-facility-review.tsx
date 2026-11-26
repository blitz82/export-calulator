import {useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {get,send} from './api'

export function FacilityRevision({prefix}:{prefix:string}){
  const qc=useQueryClient(),[selected,setSelected]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const query=useQuery({queryKey:[prefix,'facility-revisions'],queryFn:()=>get<{id:number;revision:number;data:Record<string,unknown>}[]>(prefix+'/facilities')})
  const row=query.data?.find(r=>String(r.id)===selected)
  const files=useQuery({queryKey:[prefix,'revision-files'],queryFn:()=>get<{files:{id:number;name:string}[]}>(prefix.replace(/\/sorting$/,'/settlements')+'/catalog')})
  const history=useQuery({queryKey:[prefix,'facility-history',selected],queryFn:()=>get<{id:number;data:{before_revision:number;after_revision:number;reason:string;time:string}}[]>(`${prefix}/facilities/${selected}/history`),enabled:!!selected})
  return <section><h3>시설 정보·지정기간 변경 이력</h3><p>지정번호·품목·회원은 같은 시설의 식별 기준으로 유지합니다. 변경 전·후 원문을 보존하며 자격 승인을 하지 않습니다.</p>
    <label>변경할 시설<select value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);setMessage('')}}><option value="">선택하세요</option>{query.data?.map(f=><option key={f.id} value={f.id}>{String(f.data.name)} · 개정 {f.revision}</option>)}</select></label>
    {query.isError&&<p role="alert">시설 조회 실패 <button onClick={()=>void query.refetch()}>변경 시설 다시 조회</button></p>}
    {row&&<form key={selected+':'+row.revision} onSubmit={async e=>{e.preventDefault();const fields=Object.fromEntries(new FormData(e.currentTarget));setBusy(true);setMessage('저장 중…')
      try{const {reason,...data}=fields;await send(`${prefix}/facilities/${row.id}/revise`,{revision:row.revision,reason,data:{...data,designation:row.data.designation,product:row.data.product,member_number:row.data.member_number,evidence_id:Number(data.evidence_id)}});await qc.invalidateQueries({queryKey:[prefix]});setMessage('시설 개정 저장 완료 · 지정 자격 심사는 별도')}
      catch(error){setMessage((error as Error).message)}finally{setBusy(false)}
    }}>
      <p>지정번호 {String(row.data.designation)} · {String(row.data.product)} · 회원 {String(row.data.member_number)}</p>
      {([['name','변경 시설명','text'],['operator','변경 운영·위탁주체','text'],['address','변경 주소','text'],['valid_from','변경 지정 시작일','date'],['valid_to','변경 지정 종료일','date']] as const).map(([key,label,type])=><label key={key}>{label}<input name={key} type={type} required={!['operator','address'].includes(key)} defaultValue={String(row.data[key]??'')} disabled={busy}/></label>)}
      <label>변경 지정증빙<select name="evidence_id" required defaultValue={String(row.data.evidence_id)} disabled={busy}>{!files.data?.files.some(f=>f.id===row.data.evidence_id)&&<option value={String(row.data.evidence_id)}>현재 지정증빙 유지</option>}{files.data?.files.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      <label>시설 변경 사유<input name="reason" required maxLength={500} disabled={busy}/></label><button disabled={busy}>시설 개정 저장</button>
    </form>}
    <p role="status">{message}</p>
    {history.isError&&<p role="alert">시설 이력 조회 실패 <button onClick={()=>void history.refetch()}>시설 이력 다시 조회</button></p>}
    <ul>{history.data?.map(h=><li key={h.id}>이력 {h.id} · 개정 {h.data.before_revision} → {h.data.after_revision} · {h.data.time} · {h.data.reason}</li>)}</ul>
  </section>
}
