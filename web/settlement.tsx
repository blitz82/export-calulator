import {useEffect,useState,useRef} from 'react'
import {useQuery} from '@tanstack/react-query'
import {NavLink,useSearchParams} from 'react-router-dom'
import {download,get,request,send} from './api'
import {useWorkflow,useWorkflowDirty} from './workflow'
import {defaultPolicy} from './settlement-model'
import type {Payload,Job,Catalog,Edge,Share,Ref,Result,Snapshot,Policy} from './settlement-model'
import type {Party} from './contracts-model'
import {localToday} from './contracts-model'
import {SettlementGraph} from './settlement-graph'
import {PdfView} from './pdf-view'
import {SortingArchive} from './sorting-archive'
import {SettlementInspector,SettlementEvidenceDialog} from './settlement-inspector'
import {SupportResultPanel} from './support-budget'
import {supportPolicy,money} from './support-budget-model'
import type {BudgetOverview} from './support-budget-model'

export function SettlementWorkspace({org}:{org:{id:number;name:string}}){
  const work=useWorkflow()
  const path=`org/${org.id}/settlements`
  const [params]=useSearchParams()
  const jobs=useQuery({queryKey:[path],queryFn:()=>get<Job[]>(path)})
  const [active,setActive]=useState<Job>(),[tab,setTab]=useState('물량 연결·계산')
  const catalog=useQuery({queryKey:[path,'catalog',active?.id],queryFn:()=>get<Catalog>(`${path}/catalog${active?'?job_id='+active.id:''}`),enabled:!params.get('job')||!!active})
  const members=useQuery({queryKey:[`org/${org.id}/data/members`],queryFn:()=>get<Party[]>(`org/${org.id}/data/members`)})
  const history=useQuery({queryKey:[path,'history',active?.id,active?.revision],queryFn:()=>get<Snapshot[]>(`${path}/${active!.id}/history`),enabled:!!active})
  const empty=():Payload=>({request_key:crypto.randomUUID(),title:`${work.year}년 ${Number(work.month.slice(5))}월 공동선별비`,year:Number(params.get('year'))||work.year,facility_id:Number(params.get('facility'))||0,start:params.get('start')||work.start,end:params.get('end')||work.end,policy:defaultPolicy(),lines:[],costs:[],memo:''})
  const [draft,setDraft]=useState<Payload>(empty),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[result,setResult]=useState<Result>(),[previewing,setPreviewing]=useState(false),[edge,setEdge]=useState<number>(),[pdf,setPdf]=useState<Ref>(),[snapshot,setSnapshot]=useState<Snapshot>()
  const [recordDirty,setRecordDirty]=useState(false)
  const [detailSelection,setDetailSelection]=useState<{side:'left'|'right';id:number}>(),[undoLines,setUndoLines]=useState<Edge[]>(),[policyOpen,setPolicyOpen]=useState(false),[costOpen,setCostOpen]=useState(false)
  useWorkflowDirty('settlement',dirty||recordDirty)
  const openedJob=useRef('')
  useEffect(()=>{const key=params.get('job');if(key&&openedJob.current!==key&&jobs.data){const job=jobs.data.find(j=>j.id===Number(key));if(job){openedJob.current=key;open(job)}}},[params,jobs.data])
  const facility=active?.data.facility_id??draft.facility_id
  const budgetPath=`org/${org.id}/support-budgets/${draft.year}`
  const budget=useQuery({queryKey:[budgetPath],queryFn:()=>get<BudgetOverview>(budgetPath)})
  const managed=budget.data?.plan?.data.enabled?budget.data.plan:undefined
  useEffect(()=>{if(!dirty&&!recordDirty)return;const warn=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue=''};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty,recordDirty])
  useEffect(()=>{
    if(!active)return
    const controller=new AbortController();setPreviewing(true);setResult(undefined)
    const timer=setTimeout(()=>{
      void request(`${path}/${active.id}/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft),signal:controller.signal}).then(r=>r.json()).then((r:Result)=>{if(!controller.signal.aborted){setResult(r);setPreviewing(false)}}).catch(e=>{if(!controller.signal.aborted){setMessage((e as Error).message);setPreviewing(false)}})
    },500)
    return()=>{clearTimeout(timer);controller.abort()}
  },[path,active?.id,draft,catalog.data,budget.data?.plan?.revision])
  const update=<K extends keyof Payload>(key:K,value:Payload[K])=>{setDraft(prev=>({...prev,[key]:value,request_key:crypto.randomUUID()}));setDirty(true);setResult(undefined);setSnapshot(undefined)}
  const policy=<K extends keyof Policy>(key:K,value:Policy[K])=>update('policy',{...draft.policy,[key]:value})
  function open(job?:Job){if((dirty||recordDirty)&&!window.confirm('저장하지 않은 정산 입력을 버리시겠습니까?'))return;setActive(job);setDraft(job?{...job.data,revision:job.revision,request_key:crypto.randomUUID()}:empty());setDirty(false);setRecordDirty(false);setPdf(undefined);setResult(undefined);setSnapshot(undefined);setEdge(undefined);setDetailSelection(undefined);setUndoLines(undefined);setMessage('')}
  async function refresh(){await Promise.all([catalog.refetch(),jobs.refetch(),...(active?[history.refetch()]:[])])}
  async function action(task:()=>Promise<void>){setBusy(true);setMessage('처리 중…');try{await task()}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  async function save(){const row=await send<Job>(`${path}${active?'/'+active.id:''}`,draft,active?'PUT':'POST');setActive(row);setDraft({...row.data,revision:row.revision,request_key:crypto.randomUUID()});setDirty(false);await jobs.refetch();setMessage('초안을 저장했습니다. 아직 새 물량을 예약하지 않았습니다. 기존 확정 연결이 있으면 수정 확정 전까지 유지됩니다.')}
  const editEdge=(key:keyof Edge,value:Edge[keyof Edge])=>{if(edge===undefined)return;update('lines',draft.lines.map((e,i)=>i===edge?{...e,[key]:value}:e))}
  const changeLines=(lines:Edge[])=>{setUndoLines(draft.lines);update('lines',lines)}
  const removeEdge=(index:number)=>{changeLines(draft.lines.filter((_,n)=>n!==index));setEdge(undefined);setDetailSelection(undefined);setMessage('연결을 해제했습니다. 직전 연결 변경 취소로 되돌릴 수 있습니다.')}
  const current=edge===undefined?undefined:draft.lines[edge]
  const shownResult=snapshot?.data.result??result
  const lastConfirmed=history.data?.[0]?.data.data
  const differsFromConfirmed=!!lastConfirmed&&(['title','year','facility_id','start','end','policy','lines','costs','memo','support_revision','member_costs'] as const).some(key=>JSON.stringify(draft[key])!==JSON.stringify(lastConfirmed[key]))
  const draftStatus=differsFromConfirmed?'보존된 확정 결과와 다른 초안':active?.state==='CONFIRMED'?'확정 연결의 현재 계산':dirty?'저장하지 않은 초안':'저장된 초안'
  return <div className="settlement-workspace" aria-busy={busy}>
    <div className="page-title"><h1>공동선별비 정산</h1></div>
    <section className="panel settlement-job-header"><p>증빙 등록 → 물량 연결 → 연결 확정·참고 계산 → PDF 다운로드</p><p>통합조직 품목: <b>{org.name}</b> · 적용 지원기준과 계산 결과를 원문과 대조하세요.</p>
      <div className="toolbar"><label>저장한 정산 작업<select aria-label="저장한 정산 작업" value={active?.id??''} onChange={e=>open(jobs.data?.find(j=>j.id===Number(e.target.value)))}><option value="">새 정산 작업</option>{jobs.data?.map(j=><option key={j.id} value={j.id}>{j.data.title} · {j.data.year}년 · {j.state==='CONFIRMED'?'연결 확정':'초안'}</option>)}</select></label><button type="button" disabled={busy} onClick={()=>open()}>정산 작업 만들기</button></div>
      {(jobs.isError||catalog.isError||members.isError)&&<p role="alert">{jobs.error?.message||catalog.error?.message||members.error?.message} <button type="button" onClick={()=>void refresh()}>다시 조회</button></p>}
      <details className="board-job-settings" open={!active||tab!=='물량 연결·계산'}><summary>작업 정보 · {draft.title||'새 작업'} · {draft.start} ~ {draft.end}</summary>
      <form className="toolbar" onSubmit={e=>{e.preventDefault();void action(save)}}>
        <label>정산 제목<input required value={draft.title} onChange={e=>update('title',e.target.value)}/></label>
        <label>사업연도<input type="number" min="1900" max="9999" required value={draft.year} onChange={e=>update('year',Number(e.target.value))}/></label>
        <label>공동선별장<select aria-label="정산 공동선별장" required disabled={!!active} value={facility||''} onChange={e=>update('facility_id',Number(e.target.value))}><option value="">선별장 선택</option>{catalog.data?.facilities.map(f=><option key={f.id} value={f.id}>{f.data.name}</option>)}</select></label>
        <p className="full">정산월과 연결 대상 자료의 기간은 별개입니다. 아래 기간으로 자료를 조회하며, 월을 넘는 연결은 기존 검증과 사유 확인을 적용합니다.</p><label>대상 시작일<input type="date" required value={draft.start} onChange={e=>update('start',e.target.value)}/></label><label>대상 종료일<input type="date" required value={draft.end} onChange={e=>update('end',e.target.value)}/></label>
        <button className="primary" disabled={busy||!facility||!catalog.data?.facilities.some(f=>f.id===facility)}>{active?'초안 저장':'작업 생성'}</button>
      </form></details>
      {!catalog.data?.facilities.length&&catalog.isSuccess&&<p><NavLink to="/facilities">공동선별장 명부</NavLink>에서 선별장을 먼저 등록하세요.</p>}
      {active&&<div className="toolbar" role="tablist" aria-label="공동선별 정산 단계">{['물량 연결·계산','정산 결과'].map(t=><button type="button" key={t} role="tab" aria-selected={tab===t} onClick={()=>{if(t!==tab&&recordDirty&&!window.confirm('아직 저장하지 않은 선별·비용 입력을 버리시겠습니까?'))return;setTab(t);setRecordDirty(false);setPdf(undefined)}}>{t}</button>)}</div>}
      <p role="status">{message||(!jobs.data||!catalog.data?'자료를 불러오는 중…':dirty?'저장하지 않은 변경이 있습니다.':'')}</p>
    </section>
    {active&&managed&&<section className="panel budget-job-context"><div className="section-top"><h2>{managed.data.title} · {managed.data.status} · {managed.revision}차</h2><NavLink to={`/support-budgets?year=${draft.year}`}>지원기준·배정 확인</NavLink></div><p>배정 대상: 생산자회원사 · 전체 예산 잔액 {money(budget.data?.remaining)}원 · {result?.policy?.unit_price??managed.data.unit_price}원/kg {(result?.policy?.rate_included??managed.data.rate_included)?'(최종 지원단가)':`× ${managed.data.rate}%`}</p>{result?.support?.effective_period&&<p>이번 적용: {result.support.effective_period.title} · {result.support.effective_period.start} ~ {result.support.effective_period.end} · {result.support.date_basis} 기준</p>}<p>기준·배정은 중앙 설정을 적용합니다. 아래 확인은 양쪽 물량 중 작은 값을 사용하는 계산 방식의 확인이며 실제 지급 승인이 아닙니다.</p>{!!budget.data?.pending.length&&<p role="alert">기존 확정액 {budget.data.pending.length}건의 예산 반영·제외 검토가 필요합니다.</p>}<div className="toolbar"><button type="button" disabled={busy} onClick={()=>{setDraft(prev=>({...prev,support_revision:managed.revision,policy:supportPolicy(prev.policy,managed),request_key:crypto.randomUUID()}));setDirty(true);setSnapshot(undefined);setResult(undefined)}}>지원기준·배정액 확인</button><span>{draft.support_revision===managed.revision?'현재 기준 확인됨':'최신 기준 확인 필요'}</span><button type="button" onClick={()=>void budget.refetch()}>지원기준 다시 조회</button></div></section>}
    {active&&budget.isError&&<p role="alert">지원기준 조회 실패: {budget.error.message}<button onClick={()=>void budget.refetch()}>다시 조회</button></p>}
    {active&&catalog.data&&<>
      <section className="panel settlement-source-links"><p>선별기록과 급여 증빙은 별도 메뉴에서 접수합니다. 연결 준비가 끝난 수출 물량을 아래 작업판에서 선택하세요.</p><NavLink className="button" to="/sorting-records">선별기록·증빙 등록</NavLink><NavLink className="button" to="/sorting-records">등록 자료 조회·보완</NavLink></section>
      {tab==='물량 연결·계산'&&<>
        <div className="board-workspace-grid">
        <div className="board-main">
        <SettlementGraph key={active.id} batches={catalog.data.batches.filter(b=>b.facility===facility&&(b.ready!==false||draft.lines.some(e=>e.batch_id===b.id)))} declarations={catalog.data.declarations} lines={draft.lines} start={draft.start} end={draft.end} selectedEdge={edge} onClear={()=>{setEdge(undefined);setDetailSelection(undefined)}} onChange={changeLines} onPick={index=>{setEdge(index);setDetailSelection(undefined)}} onSource={b=>{setEdge(undefined);setDetailSelection({side:'left',id:b.id})}} onDeclaration={d=>{setEdge(undefined);setDetailSelection({side:'right',id:d.id})}} onPdf={b=>{const ref=b.data.evidence?.[0];if(ref)setPdf(ref);else setMessage('선별기록 열기에서 근거 PDF를 보완하세요.')}}/>
        <div className="board-secondary-actions"><button type="button" disabled={!undoLines} onClick={()=>{if(undoLines){update('lines',undoLines);setUndoLines(undefined);setEdge(undefined);setMessage('직전 연결 변경을 취소했습니다.')}}}>직전 연결 변경 취소</button><button type="button" onClick={()=>setCostOpen(v=>!v)}>급여·비용 배부 {costOpen?'접기':'설정'}</button><button type="button" onClick={()=>setPolicyOpen(v=>!v)}>계산 기준 {policyOpen?'접기':'수정'}</button></div>
        <details className="panel board-link-table"><summary>연결 목록 표 · {draft.lines.length}건</summary><p>선별·신고 각각의 배분량과 상태를 원문과 대조하세요. 숨겨진 연결도 물량 계산에 포함됩니다.</p>
          <div className="table-scroll"><table><thead><tr><th>선별기록</th><th>수출신고 품목란</th><th>선별 / 신고 kg</th><th>작업</th></tr></thead><tbody>{draft.lines.map((e,i)=>{const b=catalog.data!.batches.find(b=>b.id===e.batch_id),d=catalog.data!.declarations.find(d=>d.id===e.declaration_id);return <tr key={e.batch_id+':'+e.declaration_id}><td>{b?.data.title??b?.data.external_id}</td><td>{d?.number} / {d?.line}<small>{d?.maker_name}</small></td><td>{e.left} / {e.right}</td><td><button type="button" onClick={()=>setEdge(i)}>연결 조정</button><button type="button" onClick={()=>removeEdge(i)}>연결 해제</button>{d?.pdf&&<PdfView org={org.id} pdf={d.pdf}/>}</td></tr>})}</tbody></table></div>
        </details></div>
        <SettlementInspector org={org.id} catalog={catalog.data} lines={draft.lines} edge={edge} selection={detailSelection} supportMembers={shownResult?.support?.members} onEdit={editEdge} onRemove={()=>{if(edge!==undefined)removeEdge(edge)}} onRecheck={()=>{if(current){const b=catalog.data!.batches.find(b=>b.id===current.batch_id),d=catalog.data!.declarations.find(d=>d.id===current.declaration_id);if(b&&d){update('lines',draft.lines.map((e,i)=>i===edge?{...e,batch_revision:b.revision,declaration_revision:d.revision}:e));setMessage('현재 원본으로 대조했습니다. 물량·근거를 확인하세요.')}}}} onPdf={setPdf} onClose={()=>{setEdge(undefined);setDetailSelection(undefined)}}/>
        </div>
        {pdf&&<SettlementEvidenceDialog path={path} catalog={catalog.data} value={pdf} onClose={()=>setPdf(undefined)}/>}
        <section className="panel board-cost-settings" hidden={!costOpen}><h2>급여·비용 배부</h2><p>같은 비용 사건은 한 번만 선택하세요. 지급·제외·다른 작업 배부를 뺀 잔액 안에서 배부합니다.</p>
          <label>배부할 비용<select aria-label="배부할 비용" value="" onChange={e=>{const c=catalog.data!.expenses.find(c=>c.id===Number(e.target.value));if(c)update('costs',[...draft.costs,{expense_id:c.id,revision:c.data.revision,method:'수동 배부액',amount:'',numerator:'',denominator:'',reason:''}])}}><option value="">비용 선택</option>{catalog.data.expenses.filter(c=>c.facility===facility&&!draft.costs.some(v=>v.expense_id===c.id)).map(c=><option key={c.id} value={c.id}>{c.data.title} · 잔액 {c.available}원</option>)}</select></label>
          {draft.costs.map((c,i)=>{const edit=(key:keyof Share,value:string|number)=>update('costs',draft.costs.map((v,n)=>n===i?{...v,[key]:value}:v));return <fieldset key={c.expense_id}><legend>{catalog.data!.expenses.find(v=>v.id===c.expense_id)?.data.title}</legend><div className="toolbar"><label>배부 방식<select value={c.method} onChange={e=>edit('method',e.target.value)}>{['직접 확인액','작업시간 비율','물량 비율','수동 배부액'].map(m=><option key={m}>{m}</option>)}</select></label>
            {c.method.includes('비율')?<><label>수출분 분자<input value={c.numerator} onChange={e=>edit('numerator',e.target.value)}/></label><label>전체 분모<input value={c.denominator} onChange={e=>edit('denominator',e.target.value)}/></label></>:<label>배부액 원<input value={c.amount} onChange={e=>edit('amount',e.target.value)}/></label>}
            <label>배부 근거·사유<input value={c.reason} onChange={e=>edit('reason',e.target.value)}/></label><button type="button" onClick={()=>update('costs',draft.costs.filter((_,n)=>n!==i))}>비용 배부 해제</button><button type="button" onClick={()=>{const row=catalog.data!.expenses.find(v=>v.id===c.expense_id);if(row)edit('revision',row.data.revision)}}>비용 원문 재대조</button></div></fieldset>})}
        </section>
        {managed?.data.formula==='실비 한도'&&<section className="panel budget-member-costs"><h2>생산자회원사별 확인 실비 배부</h2><p>이번 작업에 배부한 지급 비용을 회원사별로 나누세요. 배부 비용 합계와 일치해야 하며 같은 실비를 여러 회원사에 중복 적용하지 않습니다.</p>{[...new Map(draft.lines.flatMap(e=>{const producer=catalog.data!.batches.find(b=>b.id===e.batch_id)?.data.producer;return producer?[[producer.id,producer] as const]:[]})).values()].map(m=><label key={m.id}>{m.name} 확인 실비 (원)<input aria-label={`${m.name} 확인 실비`} inputMode="numeric" value={draft.member_costs?.find(a=>a.member_id===m.id)?.amount??''} onChange={e=>update('member_costs',[...(draft.member_costs??[]).filter(a=>a.member_id!==m.id),...(e.target.value!==''?[{member_id:m.id,amount:e.target.value}]:[])])}/></label>)}</section>}
        <section className="panel board-policy-settings" hidden={!policyOpen}><h2>참고 계산 기준</h2><p>공식 단가·보조율·허용 차이가 미정이므로 가정값을 직접 입력합니다. 기준이 없는 값은 0원으로 추정하지 않습니다.</p><div className="settlement-form">
          <label>산식<select disabled={!!managed} value={managed?.data.formula??draft.policy.formula} onChange={e=>policy('formula',e.target.value)}><option>물량 단가</option><option>실비 한도</option></select></label><label>계산 기준 이름<input disabled={!!managed} value={managed?.data.title??draft.policy.label} onChange={e=>policy('label',e.target.value)}/></label>
          {([['unit_price','kg당 단가 (원)'],['rate','보조율 (%)'],['cap','이번 작업 금액 한도 (선택)'],['cap_quantity','이번 작업 물량 한도 kg (선택)'],['scope_cap','동일 선별장·사업연도 누적 한도 원 (선택)'],['deduction','다른 지원 등 차감액 원'],['tolerance_kg','허용 절대 차이 kg (선택)'],['tolerance_percent','허용 차이율 % (신고 측 배분량 기준, 선택)']] as const).map(([k,label])=><label key={k}>{label}<input inputMode="decimal" value={managed&&['unit_price','rate','tolerance_kg','tolerance_percent'].includes(k)?String(managed.data[k as 'unit_price'|'rate'|'tolerance_kg'|'tolerance_percent']):draft.policy[k]} disabled={!!managed&&['unit_price','rate','tolerance_kg','tolerance_percent'].includes(k)||k==='rate'&&draft.policy.rate_included} onChange={e=>policy(k,e.target.value)}/></label>)}
          <label>차이 기준 결합<select disabled={!!managed} value={managed?.data.tolerance_rule??draft.policy.tolerance_rule} onChange={e=>policy('tolerance_rule',e.target.value)}><option>둘 다</option><option>하나 이상</option></select></label><label>금액 처리<select disabled={!!managed} value={managed?.data.rounding??draft.policy.rounding} onChange={e=>policy('rounding',e.target.value)}><option>원 미만 절사</option><option>원 단위 반올림</option></select></label>
          <label className="full check"><input type="checkbox" disabled={!!managed} checked={managed?.data.rate_included??draft.policy.rate_included} onChange={e=>policy('rate_included',e.target.checked)}/> 입력 단가에 보조율이 이미 포함됨 (보조율을 다시 곱하지 않음)</label>
          <label className="full">계산 근거·가정<textarea disabled={!!managed} value={managed?.data.reference??draft.policy.reference} onChange={e=>policy('reference',e.target.value)}/></label>
          <label className="full check"><input type="checkbox" checked={draft.policy.assumptions_confirmed} onChange={e=>policy('assumptions_confirmed',e.target.checked)}/> 가정값과 연결별 양쪽 물량 중 작은 값의 합계를 사용하는 참고 계산임을 확인했습니다.</label>
        </div></section>
      </>}
      {(tab==='물량 연결·계산'||tab==='정산 결과')&&shownResult?.support&&<SupportResultPanel result={shownResult.support}/>}
      {(tab==='물량 연결·계산'||tab==='정산 결과')&&<section className="panel settlement-summary"><h2>{snapshot?'보존된 정산 결과':'현재 연결·계산 결과'}</h2>
        {previewing&&!snapshot&&<p role="status">연결·잔량·계산 기준 검증 중…</p>}
        {shownResult&&<><div className="settlement-metrics"><p>선별 연결 <b>{shownResult.left} kg</b></p><p>신고 연결 <b>{shownResult.right} kg</b></p><p>차이 <b>{shownResult.difference} kg</b></p><p>적용 물량 <b>{shownResult.billable} kg</b></p><p>배부 비용 <b>{shownResult.allocated_cost}원</b></p><p>지원 예상액 <strong>{shownResult.amount===null?'기준 보완 필요':shownResult.amount+'원'}</strong></p></div>
          <p>{shownResult.label}</p><p>{shownResult.formula}</p><p>{shownResult.quantity_basis}</p>
          {shownResult.scope_available!=null&&<p>시설·연도 기존 사용액 {shownResult.scope_used}원 · 이번 계산 전 한도 잔액 {shownResult.scope_available}원</p>}
          {!!shownResult.errors.length&&<div role="alert"><b>확정 전 보완</b><ul>{shownResult.errors.map((e,i)=><li key={i}>{e}</li>)}</ul>{shownResult.errors.some(e=>e.includes('계약'))&&<NavLink to="/contracts">계약서 등록 확인</NavLink>}</div>}
          {!!shownResult.missing.length&&<p>필수 기준: {shownResult.missing.join(', ')}</p>}{shownResult.warnings.map((w,i)=><p className="muted" key={i}>{w}</p>)}
          <details><summary>연결별 잔량</summary>{shownResult.lines.map((e,i)=><p key={i}>{e.batch_title} · 선별 잔량 {e.left_remaining} kg / 신고 잔량 {e.right_remaining} kg</p>)}</details>
        </>}
        <div className={tab==='물량 연결·계산'?'board-calculation-dock':'toolbar'}>
          {tab==='물량 연결·계산'&&<div className="board-dock-amount"><span>{managed?managed.data.status+' 지원기준 적용액':'가정값에 따른 참고 지원액'}</span><strong>{previewing?'계산 중…':result?.amount!=null?Number(result.amount).toLocaleString('ko-KR')+'원':'기준 확인 필요'}</strong><small>{draft.policy.unit_price||'미입력'}원/kg · {draft.policy.rate_included?'보조율 포함 단가':(draft.policy.rate||'미입력')+'%'} · {draftStatus}</small></div>}
          {snapshot&&<button type="button" onClick={()=>setSnapshot(undefined)}>현재 초안 결과로 돌아가기</button>}<button type="button" disabled={busy} onClick={()=>void action(save)}>초안 저장</button>
          <button type="button" className="primary" disabled={busy||budget.isPending||budget.isError||previewing||!!snapshot||(!dirty&&active.state==='CONFIRMED')||!result||!!result.errors.length||!!result.missing.length} onClick={()=>void action(async()=>{
            const r=await send<{id:number;revision:number;snapshot:Snapshot['data']}>(`${path}/${active.id}/confirm`,draft)
            setActive({...active,revision:r.revision,state:'CONFIRMED',data:draft});setDraft(prev=>({...prev,revision:r.revision,request_key:crypto.randomUUID()}));setSnapshot({id:r.id,revision:r.revision,data:r.snapshot});setDirty(false);setUndoLines(undefined);setResult(r.snapshot.result);setTab('정산 결과');await refresh();setMessage('연결과 참고 계산을 확정했습니다. PDF 결과서를 다운로드할 수 있습니다.')
          })} aria-label="연결 확정·참고 계산 보존">연결 확정·참고 계산 보존</button>
          {snapshot&&<button type="button" onClick={()=>void action(async()=>{await download(`${path}/results/${snapshot.id}/pdf`);setMessage('결과 PDF 다운로드 완료')} )}>결과 PDF 다운로드</button>}
          {!snapshot&&active.state==='CONFIRMED'&&!dirty&&!differsFromConfirmed&&history.data?.[0]&&<button type="button" onClick={()=>void action(async()=>{await download(`${path}/results/${history.data![0].id}/pdf`);setMessage('보존된 결과 PDF 다운로드 완료')})}>보존 결과 PDF</button>}
        </div>
        <details><summary>확정 연결 해제</summary><p>현재 예약된 양쪽 중량과 비용을 잔량으로 환원합니다. 과거 계산·PDF는 보존합니다.</p><button type="button" disabled={busy} onClick={()=>{const reason=window.prompt('확정 해제 사유');if(reason)void action(async()=>{const r=await send<{revision:number}>(`${path}/${active.id}/release`,{revision:active.revision,reason});setActive({...active,revision:r.revision,state:'DRAFT'});setDraft(prev=>({...prev,revision:r.revision,request_key:crypto.randomUUID()}));setSnapshot(undefined);await refresh();setMessage('확정 물량과 비용을 해제했습니다. 초안 연결은 유지됩니다.')})}}>확정 연결 해제·잔량 환원</button></details>
      </section>}
      {tab==='정산 결과'&&<section className="panel"><h2>보존된 확정 결과</h2>{history.data?.map(h=><div className="toolbar" key={h.id}><span>{h.data.time} · 확정 {h.data.confirmation_number??h.revision}차 · {h.data.result.amount}원</span><button type="button" onClick={()=>setSnapshot(h)}>당시 결과 보기</button><button type="button" onClick={()=>void action(async()=>{await download(`${path}/results/${h.id}/pdf`);setMessage('보존 PDF 다운로드 완료')})}>보존 PDF 다운로드</button></div>)}{history.isError&&<p role="alert">{history.error.message}</p>}
      </section>}
    </>}
    {(!active||tab==='정산 결과')&&<SortingArchive org={org.id} onReleased={refresh}/>}
  </div>
}
