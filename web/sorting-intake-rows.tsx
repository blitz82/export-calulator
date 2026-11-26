import {useEffect,useState} from 'react'
import {send} from './api'
import {EvidenceEditor} from './settlement-records'
import type {Batch,Catalog,Ref} from './settlement-model'
import type {Party} from './contracts-model'
import {belongs,ocrInput} from './sorting-intake-model'
import type {Receipt} from './sorting-intake-model'

type Draft={revision?:number;facility_id:number;receipt_id:number;title:string;external_id:string;period_type:string;start:string;end:string;producer_id:number|null;parent_id:number|null;export_quantity:string;total:string;domestic:string;rejected:string;loss:string;unit:string;evidence:Ref[];confirmed:boolean;overlap_reason:string;memo:string;field_evidence:Record<string,Ref>;change_reason:string}
type Row={key:string;id?:number;data:Draft;dirty:boolean;error:string;readonly?:boolean}
type Candidate={file:number;page:number;field:string;value:string;serial:number}
const quantities=[['export_quantity','수출용 물량'],['total','총 선별량'],['domestic','내수'],['rejected','탈락'],['loss','감모·기타']] as const
export function IntakeRows({receipt,catalog,members,path,refresh,onView,onMessage,onDirty,candidate}:{receipt:Receipt;catalog:Catalog;members:Party[];path:string;refresh:()=>Promise<unknown>;onView:(ref:Ref)=>void;onMessage:(message:string)=>void;onDirty:(dirty:boolean)=>void;candidate?:Candidate}){
  const empty=(previous?:Draft):Draft=>({facility_id:receipt.facility,receipt_id:receipt.id,title:receipt.data.title.slice(0,190)+' 선별기록',external_id:`접수${receipt.id}-${crypto.randomUUID().slice(0,8)}`,period_type:previous?.period_type??(receipt.data.period_type==='일별'?'일별':'월별'),start:previous?.start??receipt.data.start,end:previous?.end??receipt.data.end,producer_id:previous?.producer_id??null,parent_id:null,export_quantity:'',total:'',domestic:'',rejected:'',loss:'',unit:'kg',evidence:receipt.data.documents.length===1?[{file:receipt.data.documents[0].file,page:receipt.data.documents[0].page}]:[],confirmed:false,overlap_reason:'',memo:'',field_evidence:{},change_reason:''})
  const fromBatch=(b:Batch):Row=>{const d=b.data,original=d.original_values;return {key:crypto.randomUUID(),id:b.id,dirty:false,error:'',readonly:!!d.receipt_id&&d.receipt_id!==receipt.id,data:{...empty(),revision:b.revision,title:d.title??d.external_id,external_id:d.external_id,period_type:d.period_type??'일별',start:d.start??d.work_date??'',end:d.end??d.work_date??'',producer_id:d.producer_id??d.producer?.id??null,parent_id:d.parent_id??null,unit:original?d.original_unit??'kg':'kg',export_quantity:original?.export_quantity??(d.quantity_unknown?'':b.quantity),total:original?.total??d.total??'',domestic:original?.domestic??d.domestic??'',rejected:original?.rejected??d.rejected??'',loss:original?.loss??d.loss??'',evidence:d.evidence??[],confirmed:!!d.confirmed,overlap_reason:d.overlap_reason??'',memo:d.memo??'',field_evidence:d.field_evidence??{}}}}
  const [rows,setRows]=useState<Row[]>(()=>catalog.batches.filter(b=>belongs(b.data,receipt.id)).map(fromBatch)),[selected,setSelected]=useState(''),[memberSearch,setMemberSearch]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{onDirty(rows.some(r=>r.dirty))},[rows,onDirty])
  const update=(key:string,field:keyof Draft,value:Draft[keyof Draft])=>setRows(old=>old.map(r=>r.key===key?{...r,dirty:true,error:'',data:{...r.data,[field]:value,...(field!=='confirmed'&&field!=='change_reason'?{confirmed:false}:{}),...(field==='evidence'?{field_evidence:Object.fromEntries(Object.entries(r.data.field_evidence).filter(([,ref])=>(value as Ref[]).some(v=>v.file===ref.file&&v.page===ref.page)))}:{})}}:r))
  useEffect(()=>{
    if(!candidate)return
    const row=rows.find(r=>r.key===selected)
    if(!row||row.readonly){onMessage('후보를 적용할 입력 행을 먼저 선택하세요.');return}
    if(candidate.field==='evidence'){const ref={file:candidate.file,page:candidate.page};update(row.key,'evidence',row.data.evidence.some(v=>v.file===ref.file&&v.page===ref.page)?row.data.evidence:[...row.data.evidence,ref]);onMessage('현재 PDF 페이지를 선택한 선별기록의 근거로 지정했습니다.');return}
    const input=ocrInput(candidate.field,candidate.value,row.data.unit)
    if(input===null){onMessage('단위·값이 모호하거나 선별 항목이 아닙니다. 원문을 보고 수동 입력하세요.');return}
    const ref={file:candidate.file,page:candidate.page}
    setRows(old=>old.map(r=>r.key===selected?{...r,dirty:true,error:'',data:{...r.data,[candidate.field]:input,...(candidate.field==='start'&&r.data.period_type==='일별'?{end:input}:{}),evidence:r.data.evidence.some(v=>v.file===ref.file&&v.page===ref.page)?r.data.evidence:[...r.data.evidence,ref],field_evidence:{...r.data.field_evidence,[candidate.field]:ref},confirmed:false}}:r))
    onMessage('선택한 행에 후보를 적용했습니다. 원문 대조 후 확인 체크와 저장을 완료하세요.')
    // A candidate is an explicit user event. Re-rendering the row must not reapply it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[candidate?.serial])
  async function saveRow(row:Row){
    try{
      const payload={...row.data,end:row.data.period_type==='일별'?row.data.start:row.data.end}
      const result=await send<{id:number;revision:number;issues:string[];warnings:string[];affected_jobs:{title:string}[]}>(`${path}/sources${row.id?'/'+row.id:''}`,payload,row.id?'PUT':'POST')
      setRows(old=>old.map(r=>r.key===row.key?{...r,id:result.id,dirty:false,error:'',data:{...payload,revision:result.revision,change_reason:''}}:r))
      onMessage('행 저장 완료'+(result.issues.length?' · '+result.issues.join(', '):' · 원문 확인 완료')+(result.affected_jobs.length?' · 재검토할 정산: '+result.affected_jobs.map(j=>j.title).join(', '):''));return true
    }catch(e){setRows(old=>old.map(r=>r.key===row.key?{...r,error:(e as Error).message}:r));return false}
  }
  async function save(keys:string[]){setBusy(true);try{for(const key of keys){const row=rows.find(r=>r.key===key);if(row)await saveRow(row)}await refresh()}finally{setBusy(false)}}
  return <section className="panel intake-rows novice-records"><div className="section-top"><div><h2>수출용 선별물량 입력</h2><p>생산자와 수출용 물량을 입력하세요. 빈칸은 미확인, 0은 실제 물량이 없는 경우입니다.</p></div><button type="button" disabled={busy} onClick={()=>{const row={key:crypto.randomUUID(),data:empty(rows.at(-1)?.data),dirty:true,error:''};setRows(v=>[...v,row]);setSelected(row.key)}}>선별기록 추가</button></div>
    <div className="novice-record-list">{rows.map((row,i)=><article key={row.key} className={'novice-record'+(selected===row.key?' is-selected':'')}>
      <button type="button" className="novice-record-summary" aria-expanded={selected===row.key} onClick={()=>setSelected(selected===row.key?'':row.key)}><span><b>{members.find(m=>m.id===row.data.producer_id)?.name??'생산자 선택 필요'}</b><small>{row.data.start} {row.data.period_type==='월별'?'~ '+row.data.end:''}</small></span><span>{row.data.export_quantity||'미입력'} {row.data.unit}<small>{row.dirty?'저장 전':row.data.confirmed?'원문 확인됨':'원문 확인 필요'}</small></span></button>
      {selected===row.key&&<fieldset disabled={busy||row.readonly} className="novice-record-form">
        {row.readonly&&<p>다른 자료에서 관리하는 기록입니다. 원래 등록한 자료에서 수정하세요.</p>}
        <div className="form-grid"><label>기록 구분<select aria-label={`${i+1}행 기록 구분`} value={row.data.period_type} onChange={e=>update(row.key,'period_type',e.target.value)}><option>일별</option><option>월별</option></select></label>
        <label>선별 날짜 / 시작일<input type="date" aria-label={`${i+1}행 시작일`} value={row.data.start} onChange={e=>update(row.key,'start',e.target.value)}/></label>{row.data.period_type==='월별'&&<label>선별 종료일<input type="date" aria-label={`${i+1}행 종료일`} value={row.data.end} onChange={e=>update(row.key,'end',e.target.value)}/></label>}
        <label>생산자 검색<input type="search" value={memberSearch} onChange={e=>setMemberSearch(e.target.value)} placeholder="회원사명 또는 사업자번호"/></label><label>생산자회원사<select aria-label={`${i+1}행 생산자회원사`} value={row.data.producer_id??''} onChange={e=>update(row.key,'producer_id',Number(e.target.value)||null)}><option value="">생산자를 아직 구분하지 못함</option>{members.filter(m=>(m.kind==='생산자'||m.kind==='복수')&&(m.id===row.data.producer_id||`${m.name} ${m.number}`.includes(memberSearch))).map(m=><option key={m.id} value={m.id}>{m.name} · {m.number}</option>)}</select></label>
        <label className="novice-weight">수출용 선별량<input aria-label={`${i+1}행 수출용 물량`} placeholder="원문에 적힌 수출용 물량" inputMode="decimal" value={row.data.export_quantity} onChange={e=>update(row.key,'export_quantity',e.target.value)}/></label><label>원문 단위<select aria-label={`${i+1}행 단위`} value={row.data.unit} onChange={e=>update(row.key,'unit',e.target.value)}><option>kg</option><option>t</option></select></label></div>
        <details><summary>다른 물량도 입력 (선택)</summary><div className="form-grid">{quantities.slice(1).map(([key,label])=><label key={key}>{label}<input aria-label={`${i+1}행 ${label}`} placeholder="원문에 없으면 비워두세요" inputMode="decimal" value={row.data[key]} onChange={e=>update(row.key,key,e.target.value)}/></label>)}</div></details>
        <EvidenceEditor files={catalog.files} value={row.data.evidence} onChange={v=>update(row.key,'evidence',v)} onView={onView}/>
        <details><summary>같은 물량의 월 합계·중복 자료 확인</summary><p>일별 기록과 월 합계가 같은 물량이면 두 번 계산하지 않도록 연결합니다. 별개 작업이면 관계를 지정하지 마세요.</p><label>같은 물량의 월 합계 자료<select disabled={!!row.id} value={row.data.parent_id??''} onChange={e=>update(row.key,'parent_id',Number(e.target.value)||null)}><option value="">독립된 작업</option>{catalog.batches.filter(b=>b.facility===receipt.facility&&!b.data.parent_id&&b.id!==row.id).map(b=><option key={b.id} value={b.id}>{b.data.title??b.data.external_id} · {b.quantity}kg</option>)}</select></label><label>중복 또는 합계 차이 설명<input value={row.data.overlap_reason} onChange={e=>update(row.key,'overlap_reason',e.target.value)}/></label></details>
        <details><summary>기록 이름·원본 번호·메모 (선택)</summary><label>기록 이름<input value={row.data.title} onChange={e=>update(row.key,'title',e.target.value)}/></label><label>원본 문서번호 (자동 생성, 수정 가능)<input value={row.data.external_id} onChange={e=>update(row.key,'external_id',e.target.value)}/></label><label>메모<textarea value={row.data.memo} onChange={e=>update(row.key,'memo',e.target.value)}/></label></details>
        {row.id&&<label>수정 이유<input value={row.data.change_reason} onChange={e=>update(row.key,'change_reason',e.target.value)}/></label>}
        <label className="check novice-confirm"><input type="checkbox" checked={row.data.confirmed} onChange={e=>update(row.key,'confirmed',e.target.checked)}/>PDF 원문과 생산자·물량·근거 페이지를 확인했습니다.</label>
        <div className="novice-actions"><button type="button" disabled={!row.dirty} onClick={()=>{setBusy(true);void saveRow({...row,data:{...row.data,confirmed:false}}).then(()=>refresh()).finally(()=>setBusy(false))}}>임시 저장</button><button type="button" className="primary" disabled={!row.dirty||!row.data.confirmed||!row.data.producer_id||!row.data.export_quantity||!row.data.evidence.length} onClick={()=>void save([row.key])}>원문 확인 후 저장</button>{!row.id&&<button type="button" onClick={()=>setRows(v=>v.filter(r=>r.key!==row.key))}>입력 취소</button>}</div>
        {Object.entries(row.data.field_evidence).map(([field,ref])=><button type="button" key={field} onClick={()=>onView(ref)}>{quantities.find(([k])=>k===field)?.[1]??'날짜'} 근거 {ref.page}쪽</button>)}
      </fieldset>}{row.error&&<p role="alert" className="intake-row-error">{row.error}</p>}{catalog.batches.find(b=>b.id===row.id)?.affected_jobs?.map(j=><p key={j.id}>사용 중인 정산: {j.title} · 수정 후 다시 확인해야 합니다.</p>)}
    </article>)}</div>
    {!rows.length&&<div className="novice-empty"><b>선별물량을 입력해 주세요.</b><p>선별기록 추가를 누르고 생산자별 수출용 물량을 입력하세요. PDF가 여러 개여도 같은 물량은 한 번만 입력합니다.</p></div>}
    {rows.some(r=>r.dirty)&&<div className="novice-actions"><button type="button" disabled={busy} onClick={()=>void save(rows.filter(r=>r.dirty).map(r=>r.key))}>변경사항 저장</button><small>확인하지 않은 기록은 입력 중으로 보관됩니다.</small></div>}
  </section>
}
