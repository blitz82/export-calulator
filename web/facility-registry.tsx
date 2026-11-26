import {useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {get,send,request} from './api'
import {FacilityRevision} from './sorting-facility-review'

type Facility={id:number;data:Record<string,unknown>}
export function FacilityRegistry({org}:{org:{id:number;name:string}}){
  const prefix=`org/${org.id}/sorting`
  const qc=useQueryClient()
  const facilities=useQuery({queryKey:[prefix,'facilities'],queryFn:()=>get<Facility[]>(prefix+'/facilities')})
  const [busy,setBusy]=useState(false)
  const [status,setStatus]=useState('')
  const [evidence,setEvidence]=useState('')
  const catalog=useQuery({queryKey:[prefix,'registry-files'],queryFn:()=>get<{files:{id:number;name:string}[]}>(`org/${org.id}/settlements/catalog?records_only=true`)})
  const members=useQuery({queryKey:[`org/${org.id}/data/members`],queryFn:()=>get<{name:string;number:string}[]>(`org/${org.id}/data/members`)})
  const run=async(task:()=>Promise<void>)=>{
    setBusy(true);setStatus('처리 중…')
    try{await task();await qc.invalidateQueries({queryKey:[prefix]});setStatus('서버에서 처리 결과를 확인했습니다.')}
    catch(error){setStatus((error as Error).message)}finally{setBusy(false)}
  }
  const form=(event:React.FormEvent<HTMLFormElement>)=>{event.preventDefault();return Object.fromEntries(new FormData(event.currentTarget)) as Record<string,string>}
  return <div aria-busy={busy}>
    <p role="status">{status||(facilities.isPending?'시설 명부를 불러오는 중…':'')}</p>
    {facilities.isError&&<p role="alert">시설 조회에 실패했습니다. <button onClick={()=>void facilities.refetch()}>다시 조회</button></p>}
<section className="panel"><h2>공동선별장 명부</h2><p>지정 증빙 PDF를 등록하고 운영 회원사와 유효기간을 확인하세요.</p><details><summary>기존 시설 정보 변경</summary><FacilityRevision prefix={prefix}/></details>
      <form onSubmit={e=>{const v=form(e);void run(async()=>{await send(prefix+'/facilities',{...v,evidence_id:Number(v.evidence_id)})})}}>
        {[[ 'name','시설명'],['designation','지정번호'],['operator','운영·위탁주체','optional'],['address','주소','optional'],['valid_from','지정 시작일','date'],['valid_to','지정 종료일','date']].map(([name,label,type])=><label key={name}>{label}<input name={name} type={type==='optional'?'text':type??'text'} required={type!=='optional'}/></label>)}
        <input type="hidden" name="product" value={org.name}/><p>통합조직 품목: {org.name}</p>
        <label>운영 회원사<select name="member_number" required><option value="">명부에서 선택</option>{members.data?.map(m=><option key={m.number} value={m.number}>{m.name} · {m.number}</option>)}</select></label>
        <label>지정 증빙 PDF<input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)void run(async()=>{const r=await(await request(`org/${org.id}/settlements/documents/upload?name=${encodeURIComponent(file.name)}&kind=시설지정증빙`,{method:'POST',body:file})).json() as {id:number};await catalog.refetch();setEvidence(String(r.id))})}}/></label>
        <label>지정 증빙 선택<select name="evidence_id" required value={evidence} onChange={e=>setEvidence(e.target.value)}><option value="">PDF 선택</option>{catalog.data?.files.filter(f=>f.name.toLowerCase().endsWith('.pdf')).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
        <button disabled={busy}>시설 저장</button>
      </form><ul>{facilities.data?.map(row=><li key={row.id}>{String(row.data.name)} · {String(row.data.valid_from)} ~ {String(row.data.valid_to)}</li>)}</ul>
    </section>
  </div>
}
