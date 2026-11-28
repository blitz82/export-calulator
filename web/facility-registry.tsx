import {useRef,useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {get,send,request} from './api'
import {FacilityRevision} from './sorting-facility-review'
import {useWorkflow,useWorkflowDirty} from './workflow'
type Facility={id:number;data:Record<string,unknown>}
export function FacilityRegistry({org}:{org:{id:number;name:string}}){
  const prefix=`org/${org.id}/sorting`,qc=useQueryClient(),{requestLeave}=useWorkflow()
  const facilities=useQuery({queryKey:[prefix,'facilities'],queryFn:()=>get<Facility[]>(prefix+'/facilities')})
  const catalog=useQuery({queryKey:[prefix,'registry-files'],queryFn:()=>get<{files:{id:number;name:string}[]}>(`org/${org.id}/settlements/catalog?records_only=true`)})
  const members=useQuery({queryKey:[`org/${org.id}/data/members`],queryFn:()=>get<{name:string;number:string}[]>(`org/${org.id}/data/members`)})
  const [view,setView]=useState<string|null>(null),[query,setQuery]=useState(''),[page,setPage]=useState(1),[busy,setBusy]=useState(false),[status,setStatus]=useState(''),[evidence,setEvidence]=useState(''),[dirty,setDirty]=useState(false)
  const formRef=useRef<HTMLFormElement>(null)
  useWorkflowDirty('facility',dirty,saveFacility)
  function open(next:string|null){requestLeave(()=>{setView(next);setDirty(false);setStatus('');setEvidence('')})}
  async function saveFacility(){
    if(!formRef.current?.reportValidity())return false
    const values=Object.fromEntries(new FormData(formRef.current)) as Record<string,string>
    setBusy(true);setStatus('저장 중…')
    try{await send(prefix+'/facilities',{...values,valid_to:values.valid_to||null,evidence_id:Number(values.evidence_id)});setDirty(false);await qc.invalidateQueries({queryKey:[prefix]});setView(null);setEvidence('');setStatus('선별장을 저장했습니다. 목록에서 확인할 수 있습니다.');return true}
    catch(error){setStatus((error as Error).message);throw error}finally{setBusy(false)}
  }
  const rows=(facilities.data??[]).filter(r=>[r.data.name,r.data.designation,r.data.operator,r.data.member_number].join(' ').includes(query))
  return <div aria-busy={busy}><p role="status">{status||(facilities.isPending?'선별장 목록을 불러오는 중…':dirty?'저장하지 않은 입력이 있습니다.':'')}</p>
    {(facilities.isError||catalog.isError||members.isError)&&<p role="alert">{facilities.error?.message||catalog.error?.message||members.error?.message}<button onClick={()=>{void facilities.refetch();void catalog.refetch();void members.refetch()}}>다시 조회</button></p>}
    <section className="panel"><div className="section-top"><h2>{view===null?'등록된 선별장':view==='new'?'선별장 신규 등록':'선별장 상세·수정'}</h2><div className="toolbar"><button onClick={()=>open('new')} disabled={busy}>선별장 신규 등록</button>{view!==null&&<button onClick={()=>open(null)} disabled={busy}>목록으로</button>}</div></div>
      {view===null&&<><label>선별장 검색<input type="search" value={query} onChange={e=>{setQuery(e.target.value);setPage(1)}}/></label><div className="table-scroll"><table><thead><tr><th>선별장명</th><th>지정번호</th><th>운영 회원사</th><th>등록 기간</th><th>작업</th></tr></thead><tbody>{rows.slice((page-1)*20,page*20).map(row=><tr key={row.id}><td>{String(row.data.name)}</td><td>{String(row.data.designation)}</td><td>{members.data?.find(m=>m.number.replaceAll('-','')===String(row.data.member_number).replaceAll('-',''))?.name??String(row.data.member_number)}</td><td>{String(row.data.valid_from)} ~ {String(row.data.valid_to||'종료일 미정')}</td><td><button onClick={()=>open(String(row.id))}>상세·수정</button></td></tr>)}</tbody></table></div>{!facilities.isPending&&!facilities.isError&&!rows.length&&<p>등록된 선별장이 없거나 검색 결과가 없습니다.</p>}<div className="toolbar"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>이전</button><span>{page} / {Math.max(1,Math.ceil(rows.length/20))}</span><button disabled={page*20>=rows.length} onClick={()=>setPage(p=>p+1)}>다음</button></div></>}
      {view!==null&&view!=='new'&&<FacilityRevision key={view} prefix={prefix} initialSelected={view}/>}
      {view==='new'&&<form ref={formRef} onChange={()=>setDirty(true)} onSubmit={e=>{e.preventDefault();void saveFacility().catch(()=>{})}}><p>필수 항목만 입력하면 저장할 수 있습니다. 종료일은 미정으로 두어도 됩니다.</p><fieldset disabled={busy} className="facility-form">
        <label>시설명 (필수)<input name="name" required maxLength={100}/></label><label>지정번호 (필수)<input name="designation" required maxLength={100}/></label>
        <label>지정 시작일 (필수)<input name="valid_from" type="date" required/></label><label>지정 종료일 (선택)<input name="valid_to" type="date"/></label>
        <input type="hidden" name="product" value={org.name}/><p className="full">통합조직 품목: {org.name}</p>
        <label>운영 회원사 (필수)<select name="member_number" required><option value="">명부에서 선택</option>{members.data?.map(m=><option key={m.number} value={m.number}>{m.name} · {m.number}</option>)}</select></label>
        <details className="full"><summary>운영·위탁주체와 주소 (선택)</summary><label>운영·위탁주체<input name="operator" maxLength={100}/></label><label>주소<input name="address" maxLength={200}/></label></details>
        <label className="full">지정 증빙 PDF 등록<input type="file" accept="application/pdf,.pdf" onChange={e=>{const file=e.target.files?.[0];if(file){setDirty(true);setBusy(true);void request(`org/${org.id}/settlements/documents/upload?name=${encodeURIComponent(file.name)}&kind=시설지정증빙`,{method:'POST',body:file}).then(r=>r.json()).then(async(r:{id:number})=>{await catalog.refetch();setEvidence(String(r.id));setStatus('PDF를 등록했습니다. 시설 저장을 눌러 입력을 완료하세요.')}).catch(error=>setStatus(error.message)).finally(()=>setBusy(false))}}}/></label>
        <label className="full">지정 증빙 선택 (필수)<select name="evidence_id" required value={evidence} onChange={e=>{setEvidence(e.target.value);setDirty(true)}}><option value="">PDF 선택</option>{catalog.data?.files.filter(f=>f.name.toLowerCase().endsWith('.pdf')).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
        <div className="toolbar full"><button className="primary" disabled={busy}>시설 저장</button><button type="button" onClick={()=>open(null)}>입력 취소</button></div>
      </fieldset></form>}
    </section></div>
}
