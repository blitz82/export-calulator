import {useEffect,useState} from 'react'
import {get,request,send,download} from './api'
import type {Batch,Catalog,Cost,CostData,File,Ref} from './settlement-model'
import type {Party} from './contracts-model'
import {ocrMoneyInput} from './sorting-intake-model'

export function EvidenceEditor({files,value,onChange,onView}:{files:File[];value:Ref[];onChange:(refs:Ref[])=>void;onView:(ref:Ref)=>void}){
  return <fieldset><legend>이 내용이 적힌 PDF 페이지</legend>{value.map((r,i)=><div className="toolbar" key={i}>
    <select aria-label={`근거 ${i+1} 파일`} value={r.file} onChange={e=>onChange(value.map((v,n)=>n===i?{...v,file:Number(e.target.value)}:v))} required><option value="0">PDF 선택</option>{files.filter(f=>f.name.toLowerCase().endsWith('.pdf')).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select>
    <label>쪽<input aria-label={`근거 ${i+1} 페이지`} type="number" min="1" max={files.find(f=>f.id===r.file)?.data.pages??150} value={r.page} onChange={e=>onChange(value.map((v,n)=>n===i?{...v,page:Number(e.target.value)}:v))}/></label>
    <button type="button" disabled={!r.file} onClick={()=>onView(r)}>원문 보기</button><button type="button" onClick={()=>onChange(value.filter((_,n)=>n!==i))}>제거</button>
  </div>)}<button type="button" onClick={()=>onChange([...value,{file:0,page:1}])}>PDF 페이지 추가</button><small>해당 원본 PDF를 직접 선택하고 근거 페이지를 대조하세요.</small></fieldset>
}

export function SettlementPdf({path,files,value,onClose,onCandidate,onUsePage}:{path:string;files:File[];value:Ref;onClose:()=>void;onUsePage?:(ref:Ref)=>void;onCandidate?:(candidate:{file:number;page:number;field:string;value:string})=>void}){
  const candidateLabels:Record<string,string>={export_quantity:'수출용 선별물량',domestic:'내수물량',rejected:'탈락물량',total:'총 취급·투입물량',gross:'급여·지급 총액',start:'작업일·대상기간'}
  const [page,setPage]=useState(value.page),[url,setUrl]=useState(''),[error,setError]=useState(''),[zoom,setZoom]=useState(100)
  const [extracted,setExtracted]=useState<{text:string;warning:string;method:string;candidates:{field:string;value:string;page:number}[]}[]>(),[busy,setBusy]=useState(false)
  const file=files.find(f=>f.id===value.file)
  useEffect(()=>{setPage(value.page)},[value.file,value.page])
  useEffect(()=>{
    const abort=new AbortController();let objectUrl='';setUrl('');setError('')
    void request(`${path}/documents/${value.file}/page/${page}`,{signal:abort.signal}).then(r=>r.blob()).then(b=>{
      if(abort.signal.aborted)return;objectUrl=URL.createObjectURL(b);setUrl(objectUrl)
    }).catch(e=>{if(!abort.signal.aborted)setError((e as Error).message)})
    return ()=>{abort.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
  },[path,value.file,page])
  useEffect(()=>{let active=true;setExtracted(undefined);void get<NonNullable<typeof extracted>>(`${path}/documents/${value.file}/ocr`).then(r=>{if(active)setExtracted(r)}).catch(()=>{});return()=>{active=false}},[path,value.file])
  return <section className="panel settlement-pdf"><div className="toolbar"><h2>증빙 PDF</h2><button type="button" onClick={onClose}>닫기</button></div><p>{file?.name??'원본 증빙'}</p>
    <div className="toolbar">{onUsePage&&<button type="button" className="primary" onClick={()=>onUsePage({file:value.file,page})}>이 페이지를 근거로 지정</button>}<button type="button" disabled={page<=1} onClick={()=>setPage(page-1)}>이전 쪽</button><label>증빙 페이지<input type="number" min="1" max={file?.data.pages??150} value={page} onChange={e=>{const p=Number(e.target.value);if(p>=1&&p<=(file?.data.pages??150))setPage(p)}}/></label><button type="button" disabled={page>=(file?.data.pages??150)} onClick={()=>setPage(page+1)}>다음 쪽</button>
      <label>확대<select value={zoom} onChange={e=>setZoom(Number(e.target.value))}>{[75,100,125,150,200].map(n=><option key={n}>{n}</option>)}</select></label>
      <button type="button" disabled={busy} onClick={()=>{setBusy(true);void send<NonNullable<typeof extracted>[number]>(`${path}/documents/${value.file}/ocr/${page}`).then(r=>setExtracted(prev=>[...(prev??[]),r])).catch(e=>setError((e as Error).message)).finally(()=>setBusy(false))}}>{busy?'문서에서 읽는 중…':'문서에서 값 읽기'}</button>
      <button type="button" onClick={()=>void download(`${path}/documents/${value.file}/original`).catch(e=>setError((e as Error).message))}>원본 다운로드</button>
    </div>{error&&<p role="alert">{error}</p>}<div className="contract-pdf-viewport">{url?<img src={url} alt={`선별 증빙 ${page}쪽`} style={{width:zoom+'%'}}/>:<p role="status">원문을 불러오는 중…</p>}</div>
    <details open={!!extracted?.length}><summary>문서에서 읽은 값 (직접 확인 후 반영)</summary><p>선별기록·급여 문서의 텍스트 후보입니다. 입력값과 원문을 직접 대조하세요. 재처리해도 기존 확인값은 유지됩니다.</p>{extracted?.map((r,i)=><div key={i}><p>{r.method} · {r.warning}</p>{r.candidates.map((c,n)=><p key={n}>{candidateLabels[c.field]??'추출 후보'}: <b>{c.value}</b> ({c.page}쪽) {onCandidate&&<button type="button" onClick={()=>onCandidate({file:value.file,...c})}>입력칸에 가져오기</button>}</p>)}<details><summary>추출 원문·처리 정보</summary><pre className="contract-extracted">{r.text||'읽은 내용이 없습니다. 직접 입력할 수 있습니다.'}</pre></details></div>)}</details>
  </section>
}

type SourceDraft={revision?:number;facility_id:number;title:string;external_id:string;period_type:string;start:string;end:string;producer_id:number|null;parent_id:number|null;export_quantity:string;total:string;domestic:string;rejected:string;loss:string;unit:string;evidence:Ref[];confirmed:boolean;overlap_reason:string;memo:string}
export function SettlementRecords({path,catalog,facility,members,refresh,onView,onMessage,onDirtyChange,receiptId,onlyCost=false,candidate}:{path:string;catalog:Catalog;facility:number;members:Party[];refresh:()=>Promise<unknown>;onView:(ref:Ref)=>void;onMessage:(message:string)=>void;onDirtyChange:(dirty:boolean)=>void;receiptId?:number;onlyCost?:boolean;candidate?:{file:number;page:number;field:string;value:string;serial:number}}){
  const emptySource=():SourceDraft=>({facility_id:facility,title:'',external_id:'',period_type:'일별',start:'',end:'',producer_id:null,parent_id:null,export_quantity:'',total:'',domestic:'',rejected:'',loss:'',unit:'kg',evidence:[],confirmed:false,overlap_reason:'',memo:''})
  const emptyCost=():CostData=>({revision:0,title:'선별작업 급여·비용',external_id:'비용-'+crypto.randomUUID().slice(0,8),start:'',end:'',category:'선별 인건비',gross:'',payments:[],excluded:'0',exclusion_reason:'',evidence:[],batch_ids:[],confirmed:false,field_evidence:{}})
  const [mode,setMode]=useState(onlyCost?'급여·비용':'선별기록'),[source,setSource]=useState<SourceDraft>(emptySource),[cost,setCost]=useState<CostData>(emptyCost),[sourceId,setSourceId]=useState<number>(),[costId,setCostId]=useState<number>(),[busy,setBusy]=useState(false),[kind,setKind]=useState('일별 선별일지'),[filter,setFilter]=useState(''),[offset,setOffset]=useState(0),[changeReason,setChangeReason]=useState('')
  const openCost=(row:Cost)=>{const d=row.data;setMode('급여·비용');setCost(Object.fromEntries(Object.keys(emptyCost()).map(k=>[k,d[k as keyof CostData]??emptyCost()[k as keyof CostData]])) as CostData);setCostId(row.id);setChangeReason('')}
  useEffect(()=>{
    if(!candidate||!onlyCost)return
    if(candidate.field==='evidence'){const ref={file:candidate.file,page:candidate.page};setC('evidence',cost.evidence.some(v=>v.file===ref.file&&v.page===ref.page)?cost.evidence:[...cost.evidence,ref]);onMessage('현재 PDF 페이지를 급여·비용 근거로 지정했습니다.');return}
    const value=candidate.field==='gross'?ocrMoneyInput(candidate.value):null
    if(value===null){onMessage('급여 발생액으로 확인할 수 없는 후보입니다. 원문과 대조해 수동 입력하세요.');return}
    const ref={file:candidate.file,page:candidate.page}
    setCost(v=>({...v,gross:value,confirmed:false,field_evidence:{...v.field_evidence,gross:ref},evidence:v.evidence.some(e=>e.file===ref.file&&e.page===ref.page)?v.evidence:[...v.evidence,ref]}));onDirtyChange(true);onMessage('급여 발생액 후보를 적용했습니다. 실제 지급 내역은 별도로 대조하세요.')
  },[candidate?.serial])
  const set=<K extends keyof SourceDraft>(key:K,value:SourceDraft[K])=>setSource(prev=>({...prev,[key]:value}))
  const setC=<K extends keyof CostData>(key:K,value:CostData[K])=>{onDirtyChange(true);setCost(prev=>({...prev,[key]:value,...(key!=='confirmed'?{confirmed:false}:{}),...(key==='evidence'?{field_evidence:Object.fromEntries(Object.entries(prev.field_evidence??{}).filter(([,ref])=>(value as Ref[]).some(v=>v.file===ref.file&&v.page===ref.page)))}:{})}))}
  async function action(task:()=>Promise<void>){setBusy(true);try{await task()}catch(e){onMessage((e as Error).message)}finally{setBusy(false)}}
  function editSource(row:Batch){const d=row.data;setMode('선별기록');setSourceId(row.id);setSource({revision:row.revision,facility_id:row.facility,title:d.title??d.external_id,external_id:d.external_id,period_type:d.period_type??'일별',start:d.start??d.work_date??'',end:d.end??d.work_date??'',producer_id:d.producer_id??d.producer?.id??null,parent_id:d.parent_id??null,export_quantity:row.quantity,total:d.total??'',domestic:d.domestic??'',rejected:d.rejected??'',loss:d.loss??'',unit:'kg',evidence:d.evidence??[],confirmed:!!d.confirmed,overlap_reason:d.overlap_reason??'',memo:d.memo??''})}
  const sourceRows=catalog.batches.filter(b=>b.facility===facility&&`${b.data.title} ${b.data.external_id} ${b.data.producer?.name}`.includes(filter))
  return <>
    {!onlyCost&&<section className="panel"><h2>PDF 증빙 등록</h2><div className="toolbar"><label>문서 종류<select value={kind} onChange={e=>setKind(e.target.value)}>{['일별 선별일지','월별 선별실적표','입출고 기록','급여대장','급여 지급증빙','기타 선별비 증빙'].map(k=><option key={k}>{k}</option>)}</select></label>
      <label>증빙 PDF 업로드<input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)void action(async()=>{
        const r=await(await request(`${path}/documents/upload?name=${encodeURIComponent(file.name)}&kind=${encodeURIComponent(kind)}`,{method:'POST',body:file})).json() as {id:number;duplicate:boolean}
        await refresh();const ref={file:r.id,page:1};if(mode==='선별기록')set('evidence',[...source.evidence,ref]);else setC('evidence',[...cost.evidence,ref]);onDirtyChange(true);onView(ref);onMessage(r.duplicate?'같은 PDF를 재사용합니다. 같은 물량·비용을 중복 입력하지 마세요.':'PDF 등록 완료. 원문과 OCR 후보를 확인해 입력하세요.')
      })}}/></label></div>
      <details><summary>등록 증빙 목록 ({catalog.files.length})</summary>{catalog.files.filter(f=>f.name.toLowerCase().endsWith('.pdf')).map(f=><p key={f.id}>{f.name} · {f.data.kind??'기존 증빙'} <button type="button" onClick={()=>onView({file:f.id,page:1})}>원문·OCR</button></p>)}</details>
    </section>}
    <section className="panel">{!onlyCost&&<div className="toolbar">{['선별기록','급여·비용'].map(m=><button type="button" key={m} aria-pressed={mode===m} onClick={()=>setMode(m)}>{m}</button>)}</div>}
    {mode==='선별기록'?<form onChange={()=>onDirtyChange(true)} onSubmit={e=>{e.preventDefault();void action(async()=>{
      const value={...source,facility_id:facility,end:source.period_type==='일별'?source.start:source.end};const r=await send<{warnings:string[]}>(path+'/sources'+(sourceId?'/'+sourceId:''),value,sourceId?'PUT':'POST');await refresh();setSource(emptySource());setSourceId(undefined);onDirtyChange(false);onMessage('선별기록 저장 완료. '+r.warnings.join(' '))
    })}}><h2>{sourceId?'선별기록 보완':'선별기록 입력'}</h2><fieldset disabled={busy} className="settlement-form">
      <label>선별기록 제목<input required value={source.title} onChange={e=>set('title',e.target.value)}/></label><label>원본 기록번호·구분명<input required value={source.external_id} onChange={e=>set('external_id',e.target.value)} placeholder="원본의 날짜·차수·문서번호"/></label>
      <label>기록 구분<select value={source.period_type} onChange={e=>set('period_type',e.target.value)}><option>일별</option><option>월별</option></select></label><label>생산자회원사<select aria-label="선별 생산자회원사" value={source.producer_id??''} onChange={e=>set('producer_id',Number(e.target.value)||null)}><option value="">혼합·미확인 (초안만 가능)</option>{members.filter(m=>m.kind==='생산자'||m.kind==='복수').map(m=><option key={m.id} value={m.id}>{m.name} · {m.number}</option>)}</select></label>
      <label>선별 시작일<input type="date" required value={source.start} onChange={e=>set('start',e.target.value)}/></label>{source.period_type==='월별'&&<label>선별 종료일<input type="date" required value={source.end} onChange={e=>set('end',e.target.value)}/></label>}
      <label>원본 단위<select value={source.unit} onChange={e=>set('unit',e.target.value)}><option>kg</option><option>t</option></select></label>
      <label>원집계 기록 (분할·일별 전개 시)<select aria-label="원집계 기록" disabled={!!sourceId} value={source.parent_id??''} onChange={e=>set('parent_id',Number(e.target.value)||null)}><option value="">독립된 원본 기록</option>{catalog.batches.filter(b=>b.facility===facility&&b.id!==sourceId&&!b.data.parent_id).map(b=><option key={b.id} value={b.id}>{b.data.title??b.data.external_id} · {b.quantity} kg</option>)}</select></label>
      {([['export_quantity','수출용 선별물량'],['total','총 취급·투입물량 (선택)'],['domestic','내수물량 (선택)'],['rejected','탈락물량 (선택)'],['loss','감모·기타물량 (선택)']] as const).map(([key,label])=><label key={key}>{label}<input required={key==='export_quantity'} inputMode="decimal" value={source[key]} onChange={e=>set(key,e.target.value)}/></label>)}
      <EvidenceEditor files={catalog.files} value={source.evidence} onChange={v=>set('evidence',v)} onView={onView}/>
      <label className="full">중복기간·합계 차이 확인 사유<textarea value={source.overlap_reason} onChange={e=>set('overlap_reason',e.target.value)} placeholder="일별·월별 자료의 별개 물량 여부, 합계 차이와 원문 근거"/></label>
      <label className="full">선별 메모<textarea value={source.memo} onChange={e=>set('memo',e.target.value)}/></label>
      <label className="full check"><input type="checkbox" checked={source.confirmed} onChange={e=>set('confirmed',e.target.checked)}/> 원본과 생산자·수출물량을 대조했습니다.</label>
      <div className="toolbar full"><button className="primary" disabled={!source.evidence.length}>선별기록 저장</button><button type="button" onClick={()=>{setSource(emptySource());setSourceId(undefined)}}>새 기록 입력</button></div>
    </fieldset></form>:<form onChange={()=>onDirtyChange(true)} onSubmit={e=>{e.preventDefault();void action(async()=>{
      const {revision,...value}=cost;const submitter=(e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement|null;if(submitter?.value==='draft')value.confirmed=false;const result=await send<{affected_jobs?:{title:string}[]}>(path+'/costs'+(costId?'/'+costId:''),{...value,revision:costId?revision:null,facility_id:facility,receipt_id:receiptId??null,change_reason:changeReason},costId?'PUT':'POST');await refresh();setCost(emptyCost());setCostId(undefined);setChangeReason('');onDirtyChange(false);onMessage('급여·지급증빙 저장 완료'+(result.affected_jobs?.length?' · 재검토할 정산: '+result.affected_jobs.map(j=>j.title).join(', '):''))
    })}}><h2>{costId?'급여·비용 수정':'급여·선별비 입력'}</h2><p className="novice-tip">이 화면은 선별작업에 든 급여·비용입니다. 통합조직의 회원사 지원금 지급은 「지급 내역 등록」에서 입력하세요.</p><p>같은 급여대장과 이체 증빙은 하나의 비용 내역에 함께 첨부하세요. 파일별로 비용을 중복 합산하지 않습니다.</p><fieldset disabled={busy} className="settlement-form">
      <label>비용 종류<select value={cost.category} onChange={e=>setC('category',e.target.value)}>{[...new Set(['선별 인건비','선별작업 비용','기타',cost.category])].map(v=><option key={v}>{v}</option>)}</select></label><label>급여·비용 명세서 총액 (원)<input inputMode="decimal" placeholder="모르면 비워두세요" value={cost.gross} onChange={e=>setC('gross',e.target.value)}/></label>
      <details className="full"><summary>비용 이름·문서번호·지원 제외금액 (선택)</summary>{([['title','비용 이름'],['external_id','원본 문서번호 (자동 생성)'],['excluded','지원 계산에서 뺄 금액 (원)'],['exclusion_reason','제외 이유']] as const).map(([key,label])=><label key={key}>{label}<input value={cost[key]} onChange={e=>setC(key,e.target.value)}/></label>)}</details>
      <label>비용 시작일<input type="date" value={cost.start} onChange={e=>setC('start',e.target.value)}/></label><label>비용 종료일<input type="date" value={cost.end} onChange={e=>setC('end',e.target.value)}/></label>
      <fieldset className="full"><legend>이 급여·비용을 지급한 내역</legend><p>이체확인서는 해당 비용에 추가하세요. PDF마다 비용을 새로 만들지 않습니다.</p>{cost.payments.map((p,i)=><div className="toolbar" key={i}>
        <label>이체 메모·거래번호<input required value={p.reference} onChange={e=>setC('payments',cost.payments.map((v,n)=>n===i?{...v,reference:e.target.value}:v))}/></label><label>지급일 / 반환일<input type="date" required value={p.date} onChange={e=>setC('payments',cost.payments.map((v,n)=>n===i?{...v,date:e.target.value}:v))}/></label><label>금액<input required value={p.amount} onChange={e=>setC('payments',cost.payments.map((v,n)=>n===i?{...v,amount:e.target.value}:v))}/></label>
        <label><input type="checkbox" checked={p.refund} onChange={e=>setC('payments',cost.payments.map((v,n)=>n===i?{...v,refund:e.target.checked}:v))}/>돌려받은 금액·지급 취소</label><button type="button" onClick={()=>setC('payments',cost.payments.filter((_,n)=>n!==i))}>삭제</button>
      </div>)}<button type="button" onClick={()=>setC('payments',[...cost.payments,{reference:'',date:'',amount:'',refund:false}])}>지급내역 추가</button></fieldset>
      <details className="full"><summary>관련 선별기록 선택 (여러 기록의 공통 비용인 경우)</summary>{catalog.batches.filter(b=>b.facility===facility).map(b=><label className="check" key={b.id}><input type="checkbox" checked={cost.batch_ids.includes(b.id)} onChange={e=>setC('batch_ids',e.target.checked?[...cost.batch_ids,b.id]:cost.batch_ids.filter(id=>id!==b.id))}/>{b.data.title??b.data.external_id}</label>)}</details>
      <EvidenceEditor files={catalog.files} value={cost.evidence} onChange={v=>setC('evidence',v)} onView={onView}/>
      <label className="full check"><input type="checkbox" checked={cost.confirmed} onChange={e=>setC('confirmed',e.target.checked)}/> 급여·비용과 지급 내역을 PDF 원문에서 확인했습니다.</label>
      {costId&&receiptId&&<label className="full">비용 변경 사유<input required value={changeReason} onChange={e=>setChangeReason(e.target.value)}/></label>}
      <div className="toolbar full"><button type="submit" name="saveMode" value="draft" formNoValidate>임시 저장</button><button className="primary" disabled={!cost.confirmed}>원문 확인 후 저장</button><button type="button" onClick={()=>{setCost(emptyCost());setCostId(undefined);setChangeReason('')}}>새 급여·비용 입력</button></div>
    </fieldset></form>}
    </section>
    <section className="panel">{!onlyCost&&<><h2>등록 기록</h2><label>기록 검색<input value={filter} onChange={e=>{setFilter(e.target.value);setOffset(0)}}/></label><div className="table-scroll"><table><thead><tr><th>선별기록</th><th>생산자</th><th>수출량·잔량</th><th>보완</th></tr></thead><tbody>{sourceRows.slice(offset,offset+50).map(b=><tr key={b.id}><td>{b.data.title??b.data.external_id}<small>{b.data.start??b.data.work_date} ~ {b.data.end??b.data.work_date}</small></td><td>{b.data.producer?.name??'미확인'}</td><td>{b.quantity} / {b.available} kg</td><td><button type="button" onClick={()=>editSource(b)}>선별기록 열기</button></td></tr>)}</tbody></table></div>
      <div className="toolbar"><button type="button" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>이전 기록</button><span>{sourceRows.length}건</span><button type="button" disabled={offset+50>=sourceRows.length} onClick={()=>setOffset(offset+50)}>다음 기록</button></div>
      </>}<h3>등록한 급여·비용</h3>{catalog.expenses.filter(c=>c.facility===facility).map(c=><p key={c.id}>{c.data.title} · 지급 잔액 {c.available}원 {c.issues?.length?' · '+c.issues.join(', '):' · 지급 확인'}<button type="button" disabled={!!receiptId&&!!c.data.receipt_id&&c.data.receipt_id!==receiptId} onClick={()=>openCost(c)}>지급증빙 추가·수정</button>{receiptId&&c.data.receipt_id&&c.data.receipt_id!==receiptId?<small>다른 접수에서 정정된 비용입니다. 첨부자료 탭에서 변경 이력을 확인하세요.</small>:null}</p>)}
    </section>
  </>
}
