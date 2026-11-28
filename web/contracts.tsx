import {useWorkflow,useWorkflowDirty} from './workflow'
import {NumberInput} from './number-input'
import {useEffect,useState,useRef} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {useSearchParams} from 'react-router-dom'
import {get,request,send} from './api'
import {ContractGraph} from './contract-graph'
import {ContractPdf} from './contract-pdf'
import {emptyAgreement,evidenceLabels,localToday} from './contracts-model'
import type {Agreement,AgreementData,ContractDocument,Party} from './contracts-model'

type History={id:number;revision:number;time:string;actor:string;reason:string;snapshot:Agreement}
const terms=[['supply','공급 약정'],['export','수출 약정'],['settlement','공동정산 약정'],['payment','대금 지급'],['costs','비용 부담'],['special','특약'],['memo','담당자 메모']] as const

function MemberPicker({label,members,value,onChange,disabled,snapshot}:{label:string;members:Party[];value:number|null;onChange:(id:number)=>void;disabled:boolean;snapshot?:Party}){
  const [search,setSearch]=useState('')
  const filtered=members.filter(m=>`${m.name} ${m.number}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())||m.id===value)
  return <fieldset><legend>{label}</legend>
    <label>{label} 검색<input type="search" value={search} disabled={disabled} onChange={e=>setSearch(e.target.value)} placeholder="회원사명 또는 사업자번호"/></label>
    <label>{label} 선택<select required value={value??''} disabled={disabled} onChange={e=>{if(e.target.value)onChange(Number(e.target.value))}}>
      <option value="">명부에서 선택하세요</option>{filtered.map(m=><option key={m.id} value={m.id}>{m.name} · {m.number||'사업자번호 미확인'}</option>)}
    </select></label>
    {snapshot&&<p className="muted">체결 당시: {snapshot.name} · {snapshot.number} {value===null?'(명부에서 삭제됨)':''}</p>}
  </fieldset>
}

export function ContractsWorkspace({org}:{org:{id:number;name:string}}){
  const [params,setParams]=useSearchParams()
  const memberParam=Number(params.get('member'))||undefined
  const [tab,setTab]=useState('계약 목록'),[day,setDay]=useState(localToday),[state,setState]=useState('유효')
  const [search,setSearch]=useState(''),[yearFilter,setYearFilter]=useState('')
  const path=`org/${org.id}/contracts`,qc=useQueryClient()
  const rows=useQuery({queryKey:[path,day,memberParam],queryFn:()=>get<Agreement[]>(`${path}?day=${day}&${memberParam?`member=${memberParam}`:''}`)})
  const members=useQuery({queryKey:[`org/${org.id}/data/members`],queryFn:()=>get<Party[]>(`org/${org.id}/data/members`)})
  const policies=useQuery({queryKey:[`org/${org.id}/policies`],queryFn:()=>get<{year:number;hs:string[]}[]>(`org/${org.id}/policies`)})
  const [editing,setEditing]=useState<Agreement|null>(),[draft,setDraft]=useState<AgreementData>(emptyAgreement)
  const [reason,setReason]=useState('신규 계약 등록'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[page,setPage]=useState(1)
  const [uploaded,setUploaded]=useState<ContractDocument>(),[historySnapshot,setHistorySnapshot]=useState<Agreement>()
  const [dirty,setDirty]=useState(false),formRef=useRef<HTMLFormElement>(null)
  const {requestLeave}=useWorkflow()
  useWorkflowDirty('contract',dirty,saveContract)
  const doc=useQuery({queryKey:[path,'document',draft.document_id],queryFn:()=>get<ContractDocument>(`${path}/documents/${draft.document_id}`),enabled:!!draft.document_id})
  const history=useQuery({queryKey:[path,'history',editing?.id,editing?.revision],queryFn:()=>get<History[]>(`${path}/${editing!.id}/history`),enabled:!!editing?.id})
  const document=uploaded?.id===draft.document_id?uploaded:doc.data
  const viewDocument=useQuery({queryKey:[path,'document',historySnapshot?.document_id],queryFn:()=>get<ContractDocument>(`${path}/documents/${historySnapshot!.document_id}`),enabled:!!historySnapshot})
  useEffect(()=>{
    if(!dirty)return
    const warn=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue=''}
    window.addEventListener('beforeunload',warn);return ()=>window.removeEventListener('beforeunload',warn)
  },[dirty])
  function open(row?:Agreement,amendment=false){requestLeave(()=>openNow(row,amendment))}
  function openNow(row?:Agreement,amendment=false){
    setEditing(amendment||!row?null:row)
    const data=row?{...row.data,producer_id:row.producer_id,exporter_id:row.exporter_id,document_id:row.document_id,parent_id:row.parent_id}:emptyAgreement()
    data.product=org.name
    if(amendment&&row){data.parent_id=row.id;data.document_id=0;data.review='미검토';data.evidence_pages={};data.title=row.data.title+' 변경계약'}
    if(!row&&memberParam)data.producer_id=memberParam
    setDraft(data);setUploaded(undefined);setPage(1);setReason(amendment?'변경계약 등록':row?'':'신규 계약 등록');setMessage('');setDirty(false);setHistorySnapshot(undefined)
  }
  function change<K extends keyof AgreementData>(key:K,value:AgreementData[K]){setDraft(prev=>({...prev,[key]:value,...(!['review','evidence_pages','memo'].includes(key)&&prev.review==='확인 완료'?{review:'확인 필요'}:{})}));setDirty(true)}
  function setPeriod(type:AgreementData['period_type'],year:number){
    if(!Number.isInteger(year)||year<1900||year>9999)return
    const start=type==='연간 계약'?`${year}-01-01`:draft.signed.startsWith(year+'-')?draft.signed:localToday().startsWith(year+'-')?localToday():`${year}-01-01`
    setDraft(prev=>({...prev,review:'확인 필요',period_type:type,contract_year:year,start,end:`${year}-12-31`}));setDirty(true)
  }
  async function saveContract(){
    if(!formRef.current?.reportValidity())return false
    if(!draft.document_id||!draft.producer_id||!draft.exporter_id)throw new Error('생산자회원사·수출업체와 계약 PDF를 선택하세요.')
        const {producer:_producer,exporter:_exporter,...values}=draft
        const saved=await send<Agreement>(`${path}${editing?'/'+editing.id:''}`,{...values,revision:editing?.revision??null,reason},editing?'PUT':'POST')
        setEditing(saved);setDraft({...saved.data,producer_id:saved.producer_id,exporter_id:saved.exporter_id});setDirty(false);setReason('')
        await qc.invalidateQueries({queryKey:[path]})
        setMessage(`계약 #${saved.id} 저장 완료. ${[...(saved.warnings??[]),...saved.status_issues].join(' ')}${state&&saved.state!==state?' 현재 상태 필터에서는 숨겨집니다. 전체 상태를 선택해 확인하세요.':''}`)
    return true
  }
  async function action(task:()=>Promise<void>){
    setBusy(true);setMessage('처리 중…')
    try{await task()}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
  }
  const filtered=(rows.data??[]).filter(r=>(!state||r.state===state)&&(!yearFilter||String(r.data.contract_year)===yearFilter)&&`${r.data.producer?.name} ${r.data.producer?.number} ${r.data.exporter?.name} ${r.data.exporter?.number} ${r.data.title}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const activeMember=members.data?.find(m=>m.id===memberParam)
  const hsCodes=policies.data?.find(p=>p.year===draft.contract_year)?.hs??[]
  return <div className="contracts-workspace" aria-busy={busy}>
    <section className="panel"><p>회원사 간 거래약정과 계약 PDF를 관리합니다. 수출자료 최종 확정 시 제조자와 수출화주 또는 수출대행사 중 한 업체와의 유효 계약을 확인합니다. 계약 등록만으로 실적 금액이나 지원금 적격을 판정하지 않습니다.</p>
      <div className="toolbar" role="tablist" aria-label="계약 조회 방식">{['계약 목록','회원사 연결도'].map(name=><button type="button" key={name} role="tab" aria-selected={tab===name} onClick={()=>setTab(name)}>{name}</button>)}</div>
      <p>통합조직 품목: <b>{org.name}</b> · 계약은 이 품목에 한해 관리합니다.</p>
      {memberParam&&<p>회원사별 조회: <b>{activeMember?.name??'명부에서 삭제된 회원사'}</b> · 양쪽 역할의 계약을 모두 조회합니다. <button type="button" onClick={()=>setParams({})}>전체 회원사 보기</button></p>}
      <div className="toolbar">
        <label>기준일<input type="date" required value={day} onChange={e=>{if(e.target.value)setDay(e.target.value)}}/></label>
        <label>계약 상태<select value={state} onChange={e=>setState(e.target.value)}>{['유효','예정','만료','해지','확인 필요',''].map(s=><option key={s} value={s}>{s||'전체 상태'}</option>)}</select></label>
        <label>회원사·계약 검색<input type="search" value={search} onChange={e=>setSearch(e.target.value)}/></label>
        <label>계약 연도 조회<select value={yearFilter} onChange={e=>setYearFilter(e.target.value)}><option value="">전체 연도</option>{[...new Set((rows.data??[]).map(r=>r.data.contract_year))].sort((a,b)=>b-a).map(y=><option key={y} value={y}>{y}년</option>)}</select></label>
        <button type="button" className="primary" disabled={busy} onClick={()=>open()}>계약 신규 등록</button>
      </div>
      <p className="muted">적용 기준: {day} · {state||'전체 상태'} · {yearFilter?yearFilter+'년 계약':'전체 계약 연도'} · {filtered.length}건. 확인 필요 계약은 해당 상태 또는 전체 상태에서 조회하세요.</p>
      {rows.isPending&&<p role="status">계약을 불러오는 중…</p>}
      {(rows.isError||members.isError)&&<p role="alert">{rows.error?.message||members.error?.message} <button type="button" onClick={()=>{void rows.refetch();void members.refetch()}}>다시 조회</button></p>}
    </section>
    {tab==='회원사 연결도'?<ContractGraph contracts={filtered} onOpen={r=>open(r)}/>:<section className="panel" aria-label="계약 목록">
      {!filtered.length&&!rows.isPending?<p>표시할 계약이 없습니다. 신규 계약을 등록하거나 상태·기준일 필터를 변경하세요.</p>:<div className="table-scroll"><table>
        <thead><tr><th>계약명·유형</th><th>생산자회원사</th><th>수출업체</th><th>계약 연도·기간</th><th>상태·검토</th><th>조회</th></tr></thead>
        <tbody>{filtered.map(r=><tr key={r.id}><td>{r.data.title}<small className="contract-block">#{r.id} · {r.data.kind}{r.parent_id?` · 원계약 #${r.parent_id}`:''}</small></td>
          <td>{r.data.producer?.name}<small className="contract-block">{r.data.producer?.number}</small></td><td>{r.data.exporter?.name}<small className="contract-block">{r.data.exporter?.number}</small></td>
          <td>{r.data.contract_year}년 · {r.data.period_type}<small className="contract-block">{r.data.start} ~ {r.data.end||'종료일 미정'}{r.data.auto_renew?' · 자동연장':''}</small></td>
          <td>{r.state} · {r.data.review}{r.same_business&&<small className="contract-block">동일 사업자 · 연결도 생략</small>}{r.status_issues.map(issue=><small key={issue} className="contract-block">{issue}</small>)}</td>
          <td><button type="button" disabled={busy} onClick={()=>open(r)}>상세·PDF</button><button type="button" disabled={busy} onClick={()=>open(r,true)}>변경계약</button></td></tr>)}</tbody>
      </table></div>}
    </section>}
    <p role="status" className="contract-message">{message}</p>
    {editing!==undefined&&<section className="contract-editor" aria-label="계약 입력과 PDF 대조">
      <form ref={formRef} className="panel" onSubmit={e=>{e.preventDefault();void action(async()=>{await saveContract()})}}>
        <h2>{editing?`계약 #${editing.id} · 수정 ${editing.revision}차`:'신규 계약'}{draft.parent_id?` · 원계약 #${draft.parent_id}의 변경계약`:''}</h2>
        <p>역할은 이 계약에 적용됩니다. 회원사 명부의 유형을 자동 변경하지 않습니다.</p>
        <fieldset disabled={busy} className="contract-form-fields">
          <label>계약명 (필수)<input aria-label="계약명" required maxLength={200} value={draft.title} onChange={e=>change('title',e.target.value)}/></label>
          <label>계약 유형 (필수)<input aria-label="계약 유형" required maxLength={100} value={draft.kind} onChange={e=>change('kind',e.target.value)} list="contract-types"/></label>
          <datalist id="contract-types">{['공급·수출 계약','공동정산 약정','변경계약','기타'].map(s=><option key={s}>{s}</option>)}</datalist>
          <MemberPicker label="생산자회원사" members={members.data??[]} value={draft.producer_id} disabled={!!editing} snapshot={draft.producer} onChange={id=>change('producer_id',id)}/>
          <MemberPicker label="수출업체" members={members.data??[]} value={draft.exporter_id} disabled={!!editing} snapshot={draft.exporter} onChange={id=>change('exporter_id',id)}/>
          {draft.producer_id&&draft.producer_id===draft.exporter_id&&<p>동일 회원사입니다. 계약 기록은 보존하고 연결도에서는 생략합니다.</p>}
          <label>계약 연도<input type="number" min="1900" max="9999" required value={draft.contract_year||''} onChange={e=>{const year=Number(e.target.value);if(year>=1900&&year<=9999)setPeriod(draft.period_type,year);else change('contract_year',year)}}/></label>
          <label>계약 기간 구분<select value={draft.period_type} onChange={e=>setPeriod(e.target.value as AgreementData['period_type'],draft.contract_year)}><option value="연간 계약">연간 계약 (1월 1일 ~ 12월 31일)</option><option value="기간 지정">연도 중 체결·기간 직접 지정</option></select></label>
          {(['signed','start','end','terminated'] as const).map((key,i)=><label key={key}>{['계약 체결일','계약 시작일','계약 종료일 (미정이면 공란)','해지일 (해당 시)'][i]}
            <input type="date" required={i<2} readOnly={draft.period_type==='연간 계약'&&(key==='start'||key==='end')} value={draft[key]} onChange={e=>{
              const value=e.target.value
              setDraft(prev=>({...prev,[key]:value,review:'확인 필요',...(key==='start'&&value?{contract_year:Number(value.slice(0,4))}:{}),...(key==='signed'&&prev.period_type==='기간 지정'&&value?{start:value,contract_year:Number(value.slice(0,4))}:{})}));setDirty(true)
            }}/></label>)}
          <label><input type="checkbox" checked={draft.auto_renew} onChange={e=>change('auto_renew',e.target.checked)}/> 자동연장 조항 있음</label>
          <label>통합조직 품목<input readOnly value={org.name}/></label>
          <p>대상 HS코드 ({draft.contract_year}년 정책): {hsCodes.length?hsCodes.join(', '):'미등록'} · 단일 품목에 여러 HS코드를 등록할 수 있습니다. 프로그램 설정의 연도별 정책을 따릅니다.</p>
          {policies.isError&&<p role="alert">HS코드 정책 조회 실패 <button type="button" onClick={()=>void policies.refetch()}>다시 조회</button></p>}
          <details className="contract-optional"><summary>약정 물량·금액·조항·메모 (선택)</summary><div className="optional-grid">          {(['quantity','unit','amount','currency'] as const).map((key,i)=><label key={key}>{['약정 물량 (선택)','물량 단위','거래금액 (선택)','통화'][i]}
            {key==='quantity'||key==='amount'?<NumberInput maxLength={40} value={draft[key]} onChange={e=>change(key,e.target.value)}/>:<input maxLength={20} value={draft[key]} onChange={e=>change(key,e.target.value)}/>}</label>)}
          {terms.map(([key,label])=><label key={key}>{label}<textarea rows={3} maxLength={4000} value={draft[key]} onChange={e=>change(key,e.target.value)}/></label>)}
</div></details>
          <p className="form-instructions">미검토 계약도 저장할 수 있습니다. 수출자료 최종 확정에는 유효기간과 원문 확인 완료 상태가 모두 필요합니다.</p><label>검토 상태<select value={draft.review} onChange={e=>change('review',e.target.value)}>{['미검토','확인 필요','확인 완료'].map(s=><option key={s}>{s}</option>)}</select></label>
          <fieldset><legend>계약 PDF 원본</legend><p>로컬 PDF 텍스트에서 후보를 제시합니다. 스캔본은 원문을 보고 수동 입력하세요. 후보는 자동 확정되지 않습니다.</p>
            {!editing&&<label>계약 PDF 업로드<input type="file" accept="application/pdf,.pdf" onChange={e=>{
              const file=e.target.files?.[0];if(!file)return
              void action(async()=>{
                if(file.size>20_000_000)throw Error('PDF는 20MB 이하여야 합니다.')
                const uploaded=await (await request(`${path}/documents/upload?name=${encodeURIComponent(file.name)}`,{method:'POST',body:file})).json() as ContractDocument
                setUploaded(uploaded);setPage(1);setDraft(prev=>({...prev,document_id:uploaded.id,evidence_pages:{},review:'미검토'}));setDirty(true)
                setMessage(uploaded.duplicate?'동일 PDF가 등록되어 원본을 재사용합니다. 중복 계약 여부를 확인하세요.':'PDF 등록 완료. 원문과 후보를 확인하고 계약 내용을 저장하세요.')
              })
            }}/></label>}
            {doc.isError&&<p role="alert">{doc.error.message} <button type="button" onClick={()=>void doc.refetch()}>PDF 다시 조회</button></p>}
            {document&&<><p>{document.name} · {document.pages}쪽</p>
              <details><summary>추출 후보·원문 텍스트 (미확정)</summary><p>{document.extraction.method}</p>
                {document.extraction.candidates.length===0&&<p>확인 가능한 라벨 후보가 없습니다. 원본을 보면서 직접 입력하세요.</p>}
                {document.extraction.candidates.map((c,i)=><div className="contract-candidate" key={i}><span>{c.field} · {c.page}쪽: {c.value}</span>
                  <button type="button" onClick={()=>setPage(c.page)}>근거 보기</button>
                  <button type="button" onClick={()=>{
                    setPage(c.page)
                    const role=c.field==='producer'?'생산자회원사':c.field==='exporter'?'수출업체':null
                    if(role){setMessage(`${role} 후보 “${c.value}”를 명부에서 직접 검색·선택하세요. 자동 연결하지 않습니다.`);return}
                    if(c.field==='product'){setMessage(`원문 품목 후보 “${c.value}”를 통합조직 품목 “${org.name}”과 대조하세요. 품목은 변경하지 않습니다.`);return}
                    if(!['signed','start','end','supply','export','settlement','payment','costs'].includes(c.field))return
                    const dateField=['signed','start','end'].includes(c.field)
                    if(dateField&&!/^\d{4}-\d{2}-\d{2}$/.test(c.value)){setMessage('날짜 후보는 원문 확인 후 YYYY-MM-DD 형식으로 직접 입력하세요.');return}
                    if(dateField){setDraft(prev=>({...prev,[c.field]:c.value,period_type:c.field==='start'||c.field==='end'?'기간 지정':prev.period_type,...(c.field==='start'?{contract_year:Number(c.value.slice(0,4))}:{})}));setDirty(true)}
                    else change(c.field as keyof AgreementData,c.value)
                    setMessage('후보를 입력칸에 옮겼습니다. 원문과 대조한 뒤 근거 페이지와 검토 상태를 직접 지정하세요.')
                  }}>입력 후보 확인</button></div>)}
                {document.extraction.pages.map(p=><div key={p.page}><button type="button" onClick={()=>setPage(p.page)}>{p.page}쪽 보기</button>{p.warning&&<p>{p.warning}</p>}<pre className="contract-extracted">{p.text||'추출 텍스트 없음'}</pre></div>)}
              </details>
              <p>계약 당사자와 기간을 PDF에서 확인한 뒤 확인 근거를 지정하세요.</p><button type="button" onClick={()=>change('evidence_pages',{...draft.evidence_pages,parties:page})}>현재 {page}쪽을 계약 확인 근거로 지정</button><p>{Object.keys(draft.evidence_pages).length?'확인 근거 지정됨':'확인 완료로 저장하려면 원문 근거를 1개 이상 지정하세요.'}</p><details><summary>항목별 근거 페이지 추가 (선택)</summary>              <fieldset><legend>원문 근거 페이지</legend>{Object.entries(evidenceLabels).map(([key,label])=><div className="toolbar" key={key}>
                <label>{label} 근거 쪽<input type="number" min="1" max={document.pages} value={draft.evidence_pages[key]??''} onChange={e=>{
                  const value=Number(e.target.value),pages={...draft.evidence_pages};if(e.target.value)pages[key]=value;else delete pages[key];change('evidence_pages',pages)
                }}/></label><button type="button" onClick={()=>change('evidence_pages',{...draft.evidence_pages,[key]:page})}>현재 {page}쪽 지정</button>
                {draft.evidence_pages[key]&&<button type="button" onClick={()=>setPage(draft.evidence_pages[key])}>근거 이동</button>}
              </div>)}</fieldset></details>
            </>}
          </fieldset>
          <label>{editing?'변경 사유':'등록 사유'}<input required maxLength={500} value={reason} onChange={e=>{setReason(e.target.value);setDirty(true)}}/></label>
          <div className="toolbar"><button type="submit" className="primary" disabled={busy}>계약 저장</button>
            <button type="button" onClick={()=>{requestLeave(()=>{setEditing(undefined);setDirty(false)})}}>입력 닫기</button>
            {editing&&<button type="button" onClick={()=>open(editing,true)}>변경계약으로 등록</button>}
          </div>
        </fieldset>
        {editing&&<details className="compact"><summary>변경 이력과 당시 내용</summary>
          {history.isPending&&<p role="status">이력을 불러오는 중…</p>}{history.isError&&<p role="alert">{history.error.message}</p>}
          {history.data?.map(h=><article key={h.id}><p>{h.revision}차 · {h.time} · {h.actor} · {h.reason}</p>
            <button type="button" onClick={()=>{setHistorySnapshot(h.snapshot);setPage(1)}}>당시 내용·PDF 보기</button>
          </article>)}
        </details>}
      </form>
      <div>{document&&<ContractPdf key={document.id} org={org.id} document={document} page={page} onPage={setPage}/>}</div>
    </section>}
    {historySnapshot&&<section className="panel" aria-label="과거 계약 원문"><h2>계약 #{historySnapshot.id} · {historySnapshot.revision}차 보존본</h2>
      <button type="button" onClick={()=>setHistorySnapshot(undefined)}>보존본 닫기</button>
      <p>{historySnapshot.data.title} · {historySnapshot.data.producer?.name} ↔ {historySnapshot.data.exporter?.name}</p>
      <p>{historySnapshot.data.signed} 체결 · {historySnapshot.data.start} ~ {historySnapshot.data.end||'미정'} · {historySnapshot.data.product} · {historySnapshot.data.review}</p>
      <p>{historySnapshot.data.contract_year??historySnapshot.data.start.slice(0,4)}년 · {historySnapshot.data.period_type??'기간 지정'} · 자동연장 {historySnapshot.data.auto_renew?'있음':'없음'} · 해지일 {historySnapshot.data.terminated||'해당 없음'}</p>
      <dl>{terms.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{historySnapshot.data[key]||'미입력'}</dd></div>)}</dl>
      <p>약정 물량: {historySnapshot.data.quantity||'미입력'} {historySnapshot.data.unit} · 거래금액: {historySnapshot.data.amount||'미입력'} {historySnapshot.data.currency}</p>
      <p>원문 근거: {Object.entries(historySnapshot.data.evidence_pages).map(([key,p])=>`${evidenceLabels[key]??key} ${p}쪽`).join(', ')||'미지정'}</p>
      {viewDocument.isError&&<p role="alert">{viewDocument.error.message}</p>}
      {viewDocument.data&&<ContractPdf key={'history'+viewDocument.data.id} org={org.id} document={viewDocument.data} page={page} onPage={setPage}/>}
    </section>}
  </div>
}
