import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {get,send,download} from './api'

type Legacy={id:number;revision:number;title:string;year:number;created:string;state_label:string;active_quantity:string;active_cost:string;can_release:boolean;has_active_reservations:boolean;lock_reason:string|null;paper_cases:{id:number;document_number:string;closed:boolean;report_kind:string}[]}
type Detail=Legacy&{lines:{batch_title:string;number:string;line:string;quantity:string;active:boolean}[];costs:{title:string;amount:string;active:boolean}[]}

export function SortingArchive({org,onReleased}:{org:number;onReleased:()=>Promise<unknown>}){
  const path=`org/${org}/sorting`,[open,setOpen]=useState(false),[selected,setSelected]=useState<number>(),[reason,setReason]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const list=useQuery({queryKey:[path,'archive'],queryFn:()=>get<Legacy[]>(path+'/archive'),enabled:open})
  const detail=useQuery({queryKey:[path,'archive',selected],queryFn:()=>get<Detail>(path+'/archive/'+selected),enabled:open&&!!selected})
  async function action(task:()=>Promise<void>){setBusy(true);setMessage('');try{await task()}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  return <section className="panel"><details open={open} onToggle={e=>setOpen(e.currentTarget.open)}><summary>종전 공동선별 자료·예약 보관함</summary>
    <p>종전 검토의 자료는 조회·다운로드로 보존합니다. 사용하지 않는 예약은 사유를 기록해 물량과 비용을 환원할 수 있습니다. 서면 결재·지급 원장에 잠긴 자료는 해제하지 않습니다.</p>
    {list.isPending&&open&&<p role="status">종전 자료를 불러오는 중…</p>}{list.isError&&<p role="alert">{list.error.message} <button onClick={()=>void list.refetch()}>다시 조회</button></p>}
    {list.data?.length===0&&<p>종전 공동선별 자료가 없습니다.</p>}
    <div className="table-scroll"><table><thead><tr><th>종전 검토자료</th><th>상태</th><th>남은 예약 물량·비용</th><th>조회</th></tr></thead><tbody>{list.data?.map(r=><tr key={r.id}>
      <td>{r.title}<small>{r.created}</small></td><td>{r.state_label}{r.lock_reason&&<small>서면자료 잠금·확인 필요</small>}</td><td>{r.active_quantity} kg / {r.active_cost}원</td>
      <td><button type="button" disabled={busy} onClick={()=>{setSelected(r.id);setReason('');setMessage('')}}>예약 상세 보기</button><button type="button" disabled={busy} onClick={()=>void action(async()=>{await download(`${path}/claims/${r.id}/report`);setMessage('종전 검토 Excel 다운로드 완료')})}>종전 검토 Excel</button></td>
    </tr>)}</tbody></table></div>
    {detail.isFetching&&selected&&<p role="status">예약 내역을 확인하는 중…</p>}{detail.isError&&<p role="alert">{detail.error.message}</p>}
    {detail.data&&<div className="legacy-reservation-detail"><h3>{detail.data.title} 예약 내역</h3><p>활성 예약: {detail.data.active_quantity} kg · 비용 {detail.data.active_cost}원</p>
      <div className="table-scroll"><table><thead><tr><th>선별기록</th><th>수출신고 품목란</th><th>물량</th><th>예약 상태</th></tr></thead><tbody>{detail.data.lines.map((r,i)=><tr key={i}><td>{r.batch_title}</td><td>{r.number} / {r.line}</td><td>{r.quantity} kg</td><td>{r.active?'예약 유지':'해제 이력'}</td></tr>)}</tbody></table></div>
      {detail.data.costs.map((r,i)=><p key={i}>{r.title} · {r.amount}원 · {r.active?'비용 배부 유지':'비용 해제 이력'}</p>)}
      {detail.data.lock_reason&&<p role="alert">예약 해제 제한: {detail.data.lock_reason}</p>}
      {detail.data.paper_cases.map(r=><p key={r.id}>{r.document_number} · {r.closed?'취하 보관':'서면자료 연결 유지'} <button type="button" disabled={busy} onClick={()=>void action(async()=>{await download(`${path}/paper-cases/${r.id}/report?kind=${r.report_kind}`);setMessage('종전 서면자료 다운로드 완료')})}>서면자료 조회</button></p>)}
      {detail.data.can_release?<form onSubmit={e=>{e.preventDefault();void action(async()=>{
        const r=await send<Legacy>(`${path}/archive/${detail.data!.id}/release`,{revision:detail.data!.revision,reason})
        setReason('');await Promise.all([list.refetch(),detail.refetch(),onReleased()]);setMessage(`종전 예약을 해제했습니다. 현재 예약 ${r.active_quantity} kg / ${r.active_cost}원. 원본과 해제 이력은 보존됩니다.`)
      })}}><label>종전 예약 해제 사유<input required maxLength={500} value={reason} disabled={busy} onChange={e=>setReason(e.target.value)}/></label><button className="danger" disabled={busy||!reason.trim()}>종전 예약 해제·잔량 환원</button></form>:!detail.data.has_active_reservations&&<p>해제할 활성 예약이 없습니다.</p>}
    </div>}
    <p role="status">{message}</p>
  </details></section>
}
