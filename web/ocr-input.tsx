import {NumberInput} from './number-input'
import { useEffect, useRef, useState } from 'react'
import type { PointerEvent } from 'react'
import { request } from './api'
import type { Fields, Problem, Row } from './grid'
import { ProducerSelect } from './producer-select'
import { formatLineNumber } from './line-number'
import { findMemberCandidates, MemberCandidates, memberNames, normalizeCompany, normalizeNumber } from './member-candidates'
import { OcrProfilePanel, variants, ocrFields } from './ocr-profile'
import type { Box, Preview } from './ocr-profile'

type Source = {box?:Box; alternate?:string; page?:number}
type Page = {page:number; method:string; text:string; warning:string; rows:Fields[]; evidence?:Record<string,Source>[];
  document_totals?:{total_kg?:string; fob_usd?:string}; template?:{variant:string}}
type Document = {id:number; pages:Page[]; approved:boolean}
type Review = {page:number; values:Fields; verified_usd:string; verified_kg:string; sourceIndex?:number}
type ReviewIssue = Problem & {position:number}
const makeRow = (page:number, values:Fields = {}, sourceIndex?:number):Review =>
  ({page, values:{version:'1', ...values, ...(values.line===undefined ? {} : {line:formatLineNumber(values.line)})},
    verified_usd:'', verified_kg:'', sourceIndex})
const states = ['원본에 없음', '회원사 아님']
const choices:Record<string, string[]> = {
  scope:['대상','제외','미확정'], status:['유효','취소','미확정'],
  agent_state:states, owner_state:states,
}
const exporterRoles = ['agent','owner']
const exporterRole = (key:string) => exporterRoles.find(role=>key===role || key===role+'_name')
const numericFields = ['usd','kg'] as const
const requiredFields = new Set(['number','line','version','shipped','hs','usd','kg','scope','status'])
const fieldGroups = [
  {title:'신고 기본정보', keys:['number','declared','accepted','shipped','country','version']},
  {title:'품목 및 실적', keys:['line','product','hs','quantity','unit','kg','usd']},
  {title:'업체 정보', roles:[
    {title:'수출대행자', keys:['agent_name','agent','agent_state']},
    {title:'수출화주', keys:['owner_name','owner','owner_state']},
    {title:'제조자', keys:['maker_name','maker']},
  ]},
  {title:'등록 판단', keys:['scope','status']},
]
const orderedFields = fieldGroups.flatMap(group=>group.keys ?? group.roles?.flatMap(role=>role.keys) ?? [])
const issueKey = (issue:ReviewIssue) => issue.field==='fob' ? 'usd' : issue.field==='maker_state' ? 'maker' : issue.field
const rotated = ([x,y,r,b]:Box, angle:number):Box => {
  if (angle===90) return [1-b,x,1-y,r]
  if (angle===180) return [1-r,1-b,1-x,1-y]
  if (angle===270) return [y,1-r,b,1-x]
  return [x,y,r,b]
}
const boxStyle = ([x,y,r,b]:Box) => ({left:`${x*100}%`,top:`${y*100}%`,width:`${(r-x)*100}%`,height:`${(b-y)*100}%`})

export function OcrInput({org, fields, members, disabled, onSaved}: {
  org:number; fields:Fields; members:Row[] | undefined; disabled:boolean; onSaved:(count:number, needsReview:number)=>Promise<void>
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const [dragging,setDragging] = useState(false)
  const [doc,setDoc] = useState<Document>()
  const [rows,setRows] = useState<Review[]>([])
  const [active,setActive] = useState(0)
  const [pageNumber,setPageNumber] = useState(1)
  const [zoom,setZoom] = useState(100)
  const [image,setImage] = useState('')
  const [imageError,setImageError] = useState('')
  const [busy,setBusy] = useState(false)
  const [message,setMessage] = useState('')
  const [issues,setIssues] = useState<ReviewIssue[]>([])
  const [focused,setFocused] = useState('')
  const [reading,setReading] = useState<{field:string; candidate?:string; message?:string}>()
  const [dragBox,setDragBox] = useState<Box>()
  const [managing,setManaging] = useState(false)
  const [errorField,setErrorField] = useState<string>()
  const previewArea = useRef<HTMLDivElement>(null)
  const [name,setName] = useState('')
  const [rotation,setRotation] = useState(0)
  const [selecting,setSelecting] = useState(false)
  const [selection,setSelection] = useState<Box>()
  const [profilePreview,setProfilePreview] = useState<{file:number; page:number; fields:Preview}>()
  const start = useRef<[number,number] | null>(null)
  const highlight = useRef<HTMLButtonElement>(null)
  const prefix = `org/${org}/ocr`
  const docId = doc?.id
  const page = doc?.pages[pageNumber - 1]
  const row = rows[active]
  const rowPage = row && doc?.pages[row.page - 1]
  const source = row?.sourceIndex === undefined ? undefined : rowPage?.evidence?.[row.sourceIndex]
  const currentIssues = issues.filter(issue=>issue.position===active+1)

  useEffect(()=>{
    setImage('')
    setImageError('')
    clearReading()
    setProfilePreview(undefined)
    if (docId === undefined) return
    const controller = new AbortController()
    let url = ''
    const angle = rotation ? `?rotation=${rotation}` : ''
    void request(`${prefix}/${docId}/page/${pageNumber}${angle}`, {signal:controller.signal})
      .then(result=>result.blob()).then(blob=>{
        if (controller.signal.aborted) return
        url = URL.createObjectURL(blob)
        setImage(url)
      }).catch(error=>{
        if (!controller.signal.aborted) setImageError((error as Error).message)
      })
    return ()=>{
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [docId, pageNumber, prefix, rotation])

  useEffect(()=>{
    const box = highlight.current
    const preview = previewArea.current
    if (!box || !preview) return
    const target = box.getBoundingClientRect()
    const viewport = preview.getBoundingClientRect()
    preview.scrollBy({
      top:target.top + target.height / 2 - viewport.top - viewport.height / 2,
      left:target.left + target.width / 2 - viewport.left - viewport.width / 2,
    })
  }, [focused,image,zoom])

  useEffect(()=>{
    if (!busy && errorField) {
      dialog.current?.querySelector<HTMLElement>('#' + CSS.escape('ocr-' + errorField))?.focus()
      setErrorField(undefined)
    }
  }, [busy,errorField])

  function clearReading() {
    setReading(undefined)
    setSelecting(false)
    setDragBox(undefined)
    setSelection(undefined)
    start.current = null
  }

  function beginReading(field?:string) {
    clearReading()
    setReading(field ? {field} : undefined)
    setSelecting(true)
    previewArea.current?.focus()
    previewArea.current?.scrollIntoView({block:'nearest'})
  }

  function switchView(manage:boolean) {
    clearReading()
    setProfilePreview(undefined)
    setManaging(manage)
  }

  function selectRow(index:number) {
    setActive(index)
    setPageNumber(rows[index].page)
    setFocused('')
    clearReading()
  }

  async function upload(files:FileList) {
    if (busy || !files.length) return
    if (files.length !== 1) {
      setMessage('PDF 파일을 한 번에 한 개씩 선택하세요.')
      return
    }
    const file = files[0]
    if (!/\.pdf$/i.test(file.name)) {
      setMessage('수출신고필증 PDF 파일을 선택하세요.')
      return
    }
    if (rows.length && !window.confirm('현재 OCR 검토 내용을 새 파일로 바꾸시겠습니까?')) return
    setBusy(true)
    setMessage('')
    try {
      const response = await request(`${prefix}?${new URLSearchParams({name:file.name})}`, {method:'POST', body:file})
      const result:Document = await response.json()
      const candidates = result.pages.flatMap(page=>page.rows
        .map((values,index)=>makeRow(page.page, values, index))
        .filter(item=>Object.keys(item.values).length>1))
      const first = result.pages[0]
      const initial = candidates.length ? candidates : [makeRow(first.page, first.rows[0])]
      setDoc(result)
      setName(file.name)
      setRotation(0)
      setManaging(false)
      setRows(result.approved ? [] : initial)
      setActive(0)
      setFocused('')
      clearReading()
      setPageNumber(initial[0].page)
      setIssues([])
      if (result.approved) setMessage('이미 원장에 등록한 PDF입니다.')
    } catch (error) {
      setMessage((error as Error).message)
    } finally { setBusy(false) }
  }

  function update(values:Fields) {
    setRows(current=>current.map((item,index)=> {
      if(index!==active) return item
      const next = {...item.values, ...values}
      return {...item, values:next,
        verified_usd:next.usd===item.values.usd ? item.verified_usd : '',
        verified_kg:next.kg===item.values.kg ? item.verified_kg : ''}
    }))
    setIssues(current=>current.filter(issue=>issue.position!==active+1 ||
      !(Object.hasOwn(values,issueKey(issue)) || Object.hasOwn(values,'scope') || Object.hasOwn(values,'status'))))
  }

  function updateField(field:string, value:string, fromOcr=false) {
    if (field==='usd') {
      update({usd:value,fob:value})
      return
    }
    if (field==='maker' || field==='maker_name') {
      update({[field]:value,maker_state:''})
      return
    }
    const role = exporterRole(field)
    if (!role || !row) {
      update({[field]:value})
      return
    }
    const number = normalizeNumber(field===role ? value : row.values[role])
    const registered = members?.some(member=>normalizeNumber(member.number)===number)
    const retainNonMember = !fromOcr && !registered && row.values[role+'_state']==='회원사 아님'
    update({[field]:value,[role+'_state']:retainNonMember ? '회원사 아님' : ''})
  }

  function point(event:PointerEvent<HTMLDivElement>):[number,number] {
    const rect = event.currentTarget.getBoundingClientRect()
    return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),
      Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))]
  }

  async function readSelection(box:Box, field:string) {
    setBusy(true)
    try {
      const response = await request(prefix + '/' + docId + '/page/' + pageNumber + '/selection',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({box}),
      })
      const result:{text:string;warning:string;candidates?:Fields} = await response.json()
      const candidate = result.warning ? undefined : result.candidates?.[field]
      const message = result.warning || (candidate ? undefined : result.text
        ? '이 입력칸에 맞는 값을 찾지 못했습니다.'
        : '선택 영역에서 문자를 읽지 못했습니다.')
      setReading({field,candidate,message})
    } catch (error) {
      setReading({field,message:(error as Error).message})
    } finally { setBusy(false) }
  }

  async function approve() {
    setBusy(true)
    setMessage('')
    setIssues([])
    try {
      const response = await request(prefix + '/' + docId + '/approve', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(rows),
      }, [422])
      const result = await response.json()
      if (!response.ok) {
        const errors:ReviewIssue[] = result.issues ?? []
        setIssues(errors)
        if (errors.length) goToIssue(errors[0])
        setMessage(result.detail ?? `승인을 차단하는 오류 ${errors.length}개를 확인하세요.`)
        return
      }
      setRows([])
      setDoc(undefined)
      dialog.current?.close()
      await onSaved(result.imported, result.needs_review)
    } catch (error) {
      setMessage((error as Error).message)
    } finally { setBusy(false) }
  }

  function close() {
    if (busy || (rows.length && !window.confirm('검토 중인 입력을 닫으시겠습니까? 수정값은 저장되지 않습니다.'))) return
    dialog.current?.close()
    setDoc(undefined)
    setRows([])
    setIssues([])
    setMessage('')
    switchView(false)
    dragDepth.current = 0
    setDragging(false)
  }

  function viewEvidence(evidence:Source | undefined, fallback=row?.page) {
    if (evidence?.box && fallback !== undefined) setPageNumber(evidence.page ?? fallback)
  }

  function openField(key:string, focusInput=false) {
    if (key !== focused) clearReading()
    setFocused(key)
    viewEvidence(source?.[key])
    if (focusInput) setErrorField(key)
  }

  function goToIssue(issue:ReviewIssue) {
    const index = issue.position-1
    const key = issueKey(issue)
    selectRow(index)
    setFocused(key)
    setErrorField(key)
    const target = rows[index]
    if (target.sourceIndex !== undefined) {
      viewEvidence(doc?.pages[target.page-1]?.evidence?.[target.sourceIndex]?.[key],target.page)
    }
  }

  const hasOtherIssueRows = new Set(issues.map(issue=>issue.position)).size>1
  const activeBox = dragBox ?? (selection && rotated(selection,rotation))
  const excluded = row?.values.scope==='제외' || row?.values.status==='취소'
  const showVersion = !!row && (row.values.version!=='1' || focused==='version' ||
    currentIssues.some(issue=>issueKey(issue)==='version'))
  const needsAttention = row ? orderedFields.filter(key=> {
    if (currentIssues.some(issue=>issueKey(issue)===key)) return true
    if (key==='kg' || key==='usd') return !row[key==='kg' ? 'verified_kg' : 'verified_usd']
    if (excluded) return false
    if ((key==='scope' || key==='status') && row.values[key]==='미확정') return true
    const original = row.sourceIndex===undefined ? undefined : rowPage?.rows[row.sourceIndex]?.[key]
    return (requiredFields.has(key) && !row.values[key]?.trim()) ||
      (!!source?.[key]?.alternate && row.values[key]===original)
  }) : []

  function nextAttention() {
    const position = orderedFields.indexOf(focused)
    const next = needsAttention.find(key=>orderedFields.indexOf(key)>position) ?? needsAttention[0]
    if (next) openField(next,true)
  }

  function exporterCandidates(role:string,key:string,value:string,values:Fields) {
    const number = normalizeNumber(values[role])
    const name = normalizeCompany(values[role+'_name'])
    const registered = number.length===10 && members?.some(member=>
      normalizeNumber(member.number)===number && memberNames(member).includes(name))
    return registered ? [] : findMemberCandidates(members,value,key===role ? 'number' : 'name')
  }

  function fieldRow(key:string) {
    if (!row) return null
    const problems = currentIssues.filter(issue=>issueKey(issue)===key)
    const label = key==='usd' ? '품목란 FOB(USD)' : key==='line' ? '품목란 번호' : fields[key]
    const id = 'ocr-' + key
    const value = row.values[key] ?? ''
    const original = row.sourceIndex===undefined ? undefined : rowPage?.rows[row.sourceIndex]?.[key]
    const picked = source?.[key]
    const numeric = numericFields.find(field=>field===key)
    const verified = numeric && !!row[numeric==='usd' ? 'verified_usd' : 'verified_kg']
    const mismatch = !verified && !!picked?.alternate && value===original
    const undecided = !excluded && (key==='scope' || key==='status') && value==='미확정'
    const required = requiredFields.has(key) && (!excluded || !!numeric)
    const status = problems.length || undecided ? '확인 필요' : !value ? (required ? '입력 필요' : '미입력') :
      mismatch ? '값 불일치' : numeric ? (verified ? '확인 완료' : '원본 확인 필요') : ''
    const alerted = !!problems.length || undecided || (required && !value) || mismatch || (!!numeric && !verified)
    const expanded = focused===key
    const role = expanded ? exporterRole(key) : undefined
    const candidates = role ? exporterCandidates(role,key,value,row.values) : []
    return <div className={'ocr-field-row' + (expanded ? ' ocr-field-open' : '')} key={key}>
      <button type="button" className="ocr-field-summary" aria-expanded={expanded} aria-controls={id + '-editor'}
        onClick={()=>{
          if (expanded) {
            setFocused('')
            clearReading()
          } else openField(key,true)
        }}>
        <span>{label}</span>
        <span className={numeric || key==='quantity' ? 'ocr-number' : undefined}>{value || '—'}</span>
        <span className={alerted ? 'ocr-field-alert' : 'ocr-field-state'}>{status}</span>
      </button>
      <div id={id + '-editor'} className="ocr-field-editor" hidden={!expanded}>
        {expanded && <>
          {key==='maker' && <ProducerSelect members={members} onSelect={member=>
            update({maker:String(member.number),maker_name:String(member.name),maker_state:''})}/>}
          {choices[key] ? <select id={id} aria-label={label} value={value} onChange={e=>update({[key]:e.target.value})}
            aria-invalid={!!problems.length} aria-describedby={problems.length ? id + '-issues' : undefined}>
            <option value="">{key.endsWith('_state') ? '회원 명부 대조' : '선택'}</option>
            {choices[key].map(option=><option key={option}>{option}</option>)}
          </select> : ['usd','kg','fob','quantity'].includes(key)?<NumberInput id={id} aria-label={label} value={value} aria-invalid={!!problems.length} aria-describedby={problems.length?id+'-issues':undefined} onChange={e=>updateField(key,e.target.value)}/>:<input id={id} aria-label={label} value={value}
            aria-invalid={!!problems.length} aria-describedby={problems.length ? id + '-issues' : undefined}
            onChange={e=>updateField(key,e.target.value)} onBlur={key==='line' ? e=>{
              const formatted = formatLineNumber(e.currentTarget.value)
              if (formatted!==e.currentTarget.value) updateField(key,formatted)
            } : undefined}/>}
          {role && <MemberCandidates members={candidates} label={label} onSelect={member=>{
            update({[role+'_name']:String(member.name),[role]:String(member.number),[role+'_state']:''})
            clearReading()
            setErrorField(key)
          }}/>}
          {key==='scope' && <small>이 품목란이 공동정산에 포함되는지 선택하세요.</small>}
          {mismatch && <small>다른 추출 후보 · {picked.alternate}</small>}
          {numeric && <label className="ocr-confirm">
            <input type="checkbox" checked={verified} disabled={!value.trim()}
              onChange={event=>{
                const checked = event.target.checked
                setRows(current=>current.map((item,index)=>index===active ?
                  {...item,[`verified_${numeric}`]:checked ? value : ''} : item))
                setIssues(current=>current.filter(issue=>issue.position!==active+1 || issueKey(issue)!==key))
              }}/>{label} 원본 확인
          </label>}
          {Object.hasOwn(ocrFields,key) && <div className="toolbar ocr-field-tools">
            {picked?.box ? <button type="button" onClick={()=>{
              viewEvidence(source?.[key])
              highlight.current?.scrollIntoView({block:'center',inline:'center'})
            }}>원본 위치 보기</button> : <small>원본 위치 없음</small>}
            <button type="button" disabled={!image} aria-label={label + ' PDF에서 읽기'}
              aria-pressed={selecting && reading?.field===key} onClick={()=>beginReading(key)}>PDF에서 읽기</button>
          </div>}
          {reading?.field===key && <div className="ocr-field-candidate" aria-live="polite">
            {busy && <small>선택 영역을 읽는 중…</small>}
            {reading.message && <small>{reading.message} 원본을 보고 직접 입력할 수 있습니다.</small>}
            {reading.candidate && <><span>후보 · {reading.candidate}</span>{' '}
              <button type="button" onClick={()=>{
                if (!reading.candidate) return
                updateField(key,reading.candidate,true)
                clearReading()
                setErrorField(key)
              }}>후보 사용</button></>}
          </div>}
          {!!problems.length && <div className="ocr-field-alert" id={id + '-issues'}>
            {problems.map((problem,index)=><small key={index}>{problem.message}</small>)}
          </div>}
        </>}
      </div>
    </div>
  }

  return <>
    <button type="button" disabled={disabled} onClick={()=>dialog.current?.showModal()}>PDF에서 가져오기</button>
    <dialog ref={dialog} className="ocr-dialog" aria-labelledby="ocr-title"
      onDragEnter={event=>{
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        dragDepth.current += 1
        if (!busy) setDragging(true)
      }}
      onDragOver={event=>{
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        event.dataTransfer.dropEffect = busy ? 'none' : 'copy'
      }}
      onDragLeave={()=>{
        dragDepth.current = Math.max(0, dragDepth.current-1)
        if (!dragDepth.current) setDragging(false)
      }}
      onDrop={event=>{
        event.preventDefault()
        dragDepth.current = 0
        setDragging(false)
        if (!managing) void upload(event.dataTransfer.files)
      }}
      onCancel={event=>{
        event.preventDefault()
        if (selecting) clearReading()
        else close()
      }}>
      <header className="toolbar">
        <h2 id="ocr-title">{managing ? "OCR 서식 관리" : "PDF에서 가져오기"}</h2>
        <span className="toolbar-spacer"/>
        {doc && <button type="button" disabled={busy} onClick={()=>switchView(!managing)}>
          {managing ? 'PDF 검토로 돌아가기' : 'OCR 서식 관리'}
        </button>}
        <button type="button" disabled={busy} onClick={close}>닫기</button>
      </header>
      {!managing && <><div className={`ocr-upload${dragging && !busy ? ' ocr-upload-active' : ''}`}>
        <p>수출신고필증 PDF를 여기에 끌어다 놓으세요.</p>
        <button type="button" autoFocus disabled={busy} onClick={()=>fileInput.current?.click()}>파일 찾기</button>
        <input ref={fileInput} type="file" hidden aria-label="수출신고필증 PDF" accept=".pdf,application/pdf" disabled={busy}
          onChange={event=>{
            const files = event.currentTarget.files
            if (files) void upload(files)
            event.currentTarget.value = ''
          }}/>
      </div>
      {!doc && <p>수출신고필증 PDF를 올려 주세요. <strong>UNI-PASS 전자 PDF</strong>를 사용하면 더 정확하게 추출할 수 있습니다.</p>}
      <p>등록하기 전에 <strong>모든 추출값을 원본과 반드시 대조</strong>하고, 빠지거나 잘못 읽힌 값은 수정하세요. 금액과 순중량의 원본 확인란을 체크한 뒤 승인하세요.</p></>}
      {busy && <p role="status">PDF 또는 승인 요청을 처리하고 있습니다…</p>}
      {message && <p role="alert">{message}</p>}
      {doc && <>
        <p><strong>{name}</strong> · {doc.pages.length}쪽 · 검토 품목 {rows.length}개</p>
        <div className="ocr-panels">
        <section aria-label="원본 PDF">
          <div className="toolbar">
            <button aria-label="이전 쪽" disabled={busy || pageNumber<=1} onClick={()=>setPageNumber(pageNumber-1)}>이전</button>
            <span>{pageNumber} / {doc.pages.length}쪽</span>
            <button aria-label="다음 쪽" disabled={busy || pageNumber>=doc.pages.length} onClick={()=>setPageNumber(pageNumber+1)}>다음</button>
            <label>확대 <select value={zoom} onChange={e=>setZoom(Number(e.target.value))}>
              {[75,100,125,150,200].map(value=><option key={value} value={value}>{value}%</option>)}
            </select></label>
            <button type="button" disabled={busy} onClick={()=>setRotation((rotation+90)%360)}>오른쪽으로 90° 회전</button>
            {managing && <button type="button" disabled={!image || busy} aria-pressed={selecting}
              onClick={()=>selecting ? clearReading() : beginReading()}>원본 영역 지정</button>}
          </div>
          {managing && page?.template && <p>{variants[page.template.variant] ?? '수출신고필증'} · {page.method}</p>}
          {selecting && <div className="toolbar">
            <span role="status">{reading ? ocrFields[reading.field] + ' 값이 있는 영역을 드래그하세요.' : '서식에 저장할 영역을 드래그하세요.'}</span>
            <button type="button" onClick={clearReading}>영역 지정 취소</button>
          </div>}
          <div className="ocr-page" ref={previewArea} tabIndex={-1} aria-label="PDF 미리보기">
            {image ? <div className={`ocr-image${selecting ? ' ocr-selecting' : ''}`} style={{width:`${zoom}%`}}
              onPointerDown={event=>{
                if (!selecting || event.button!==0) return
                start.current = point(event)
                event.currentTarget.setPointerCapture(event.pointerId)
              }}
              onPointerMove={event=>{
                if (!start.current) return
                const [a,b] = start.current
                const [x,y] = point(event)
                setDragBox([Math.min(x,a),Math.min(y,b),Math.max(x,a),Math.max(y,b)])
              }}
              onPointerUp={event=>{
                const origin = start.current
                start.current = null
                if (!selecting || !origin) return
                const [x,y] = point(event)
                const [a,b] = origin
                setDragBox(undefined)
                if (Math.abs(x-a)<.002 || Math.abs(y-b)<.002) return
                const box = rotated([Math.min(x,a),Math.min(y,b),Math.max(x,a),Math.max(y,b)],(360-rotation)%360)
                setSelection(box)
                setSelecting(false)
                if (reading) void readSelection(box,reading.field)
              }}
              onPointerCancel={()=>{start.current=null; setDragBox(undefined)}}>
              <img alt={`수출신고필증 원본 ${pageNumber}쪽`} src={image} draggable={false}/>
              {activeBox && <span className="ocr-selected-box" style={boxStyle(activeBox)}/>}
              {profilePreview?.file===doc.id && profilePreview.page===pageNumber &&
                Object.entries(profilePreview.fields).map(([key,item])=><span className="ocr-selected-box" key={key}
                  title={fields[key] ?? key} style={boxStyle(rotated(item.box,rotation))}/>)}
              {Object.entries(source ?? {}).map(([key,item])=>{
                if (managing || selecting || !orderedFields.includes(key) || !item.box || pageNumber !== (item.page ?? row?.page)) return null
                return <button type="button" key={key} ref={focused===key ? highlight : null}
                  className={`ocr-location${focused===key ? ' ocr-location-active' : ''}`}
                  aria-label={`${key==='serial' ? '일련번호' : fields[key] ?? key} 원본 위치`}
                  style={boxStyle(rotated(item.box,rotation))}
                  onClick={()=>{if (orderedFields.includes(key)) openField(key,true)}}/>
              })}
            </div>
              : <p role={imageError?'alert':'status'}>{imageError || '원본을 불러오는 중…'}</p>}
          </div>
          {page?.warning && <p role="status">{page.warning}</p>}
          {!!(page?.document_totals?.total_kg || page?.document_totals?.fob_usd) && <p>
            문서 전체 합계{page.document_totals.total_kg && ` · 총중량 ${page.document_totals.total_kg} kg`}
            {page.document_totals.fob_usd && ` · 총 FOB ${page.document_totals.fob_usd} USD`}
          </p>}
          {managing && !!page?.text && <details><summary>추출 문자 보기 · {page.method}</summary><pre>{page.text}</pre></details>}
        </section>
        {managing ? <OcrProfilePanel key={prefix + '/' + doc.id} prefix={prefix} file={doc.id} page={pageNumber}
          reviewBusy={busy} variant={page?.template?.variant} scan={page?.method?.includes('Windows OCR') ?? false}
          selection={selection} onBusyChange={setBusy} onPreview={fields=>setProfilePreview({file:doc.id,page:pageNumber,fields})}/>
          : !doc.approved && <section className="ocr-review" aria-label="품목란 검토">
          <div className="ocr-review-heading">
          {row && <div className="ocr-review-context">
            <strong>신고번호 {row.values.number || '미입력'}</strong>
            <span>품목란 {row.values.line || '미입력'} · 원본 {row.page}쪽</span>
          </div>}
          <div className="toolbar">
            <label>검토 품목란 <select value={active} disabled={busy} onChange={e=>selectRow(Number(e.target.value))}>
              {rows.map((item,index)=><option key={index} value={index}>
                {index+1}. {item.page}쪽 · {item.values.line || '품목란 번호 미입력'}
              </option>)}
            </select></label>
            <button disabled={busy || rows.length>=300} onClick={()=>{
              clearReading()
              setFocused('')
              setActive(rows.length)
              const header = page?.rows.length===1 ? page.rows[0] : undefined
              setRows([...rows,makeRow(pageNumber,header?.line?{}:header)])
              setIssues([])
            }}>현재 쪽 품목란 추가</button>
            <button disabled={busy || rows.length<=1} onClick={()=>{
              clearReading()
              setFocused('')
              const next = rows.filter((_,index)=>index!==active)
              const index = Math.min(active,next.length-1)
              setRows(next)
              setActive(index)
              setPageNumber(next[index].page)
              setIssues([])
            }}>현재 품목란 제외</button>
          </div>
          {row && <div className="ocr-review-progress">
            <span role="status">확인 필요 {needsAttention.length}개</span>
            <button type="button" disabled={busy || !needsAttention.length} onClick={nextAttention}>다음 확인 항목</button>
          </div>}
          </div>
          {row && <fieldset className="ocr-review-fields" disabled={busy} aria-label="품목란 입력">
            {fieldGroups.map(group=><section className="ocr-field-group" key={group.title} aria-label={group.title}>
              <h3>{group.title}</h3>
              {group.keys?.filter(key=>key!=='version' || showVersion).map(fieldRow)}
              {group.roles?.map(role=><section className="ocr-role" key={role.title} aria-label={role.title}>
                <h4>{role.title}</h4>
                {role.keys.map(fieldRow)}
              </section>)}
            </section>)}
            <details className="ocr-review-extra">
              <summary>기타 정보</summary>
              <p>일련번호 {row.values.serial || '없음'}</p>
              {!showVersion && <button type="button" onClick={()=>openField('version',true)}>정정 버전 {row.values.version} 수정</button>}
            </details>
          </fieldset>}
          <footer className="toolbar">
            <span>{rows.filter(item=>item.verified_usd && item.verified_kg).length} / {rows.length}개 품목란 수치 확인</span>
            {hasOtherIssueRows && <button type="button" disabled={busy} onClick={()=>{
              const issue = issues.find(item=>item.position>active+1) ?? issues.find(item=>item.position!==active+1)
              if (issue) goToIssue(issue)
            }}>다음 오류 품목란</button>}
            <button className="primary" disabled={busy || !rows.length} onClick={()=>void approve()}>승인하고 원장에 등록</button>
          </footer>
        </section>}
        </div>
      </>}
    </dialog>
  </>
}
