import { useEffect, useRef, useState } from 'react'
import { request } from './api'
import type { Fields, Problem, Row } from './grid'
import { ProducerSelect } from './producer-select'

type Page = {page:number; method:string; text:string; warning:string; rows:Fields[]}
type Document = {id:number; pages:Page[]; approved:boolean}
type Review = {page:number; values:Fields; verified_usd:string; verified_kg:string}
type ReviewIssue = Problem & {position:number}
const makeRow = (page:number, values:Fields = {}):Review =>
  ({page, values:{version:'1', ...values}, verified_usd:'', verified_kg:''})
const states = ['원본에 없음', '회원사 아님']
const choices:Record<string, string[]> = {
  scope:['대상','제외','미확정'], status:['유효','취소','미확정'],
  agent_state:states, owner_state:states,
}
const numericFields = ['usd','kg'] as const

export function OcrInput({org, fields, members, disabled, onSaved}: {
  org:number; fields:Fields; members:Row[] | undefined; disabled:boolean; onSaved:(count:number)=>Promise<void>
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
  const prefix = `org/${org}/ocr`
  const docId = doc?.id
  const page = doc?.pages[pageNumber - 1]
  const row = rows[active]
  const currentIssues = issues.filter(issue=>issue.position===active+1)

  useEffect(()=>{
    setImage('')
    setImageError('')
    if (docId === undefined) return
    const controller = new AbortController()
    let url = ''
    void request(`${prefix}/${docId}/page/${pageNumber}`, {signal:controller.signal})
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
  }, [docId, pageNumber, prefix])

  function selectRow(index:number) {
    setActive(index)
    setPageNumber(rows[index].page)
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
      const candidates = result.pages.flatMap(page=>page.rows.filter(values=>values.line)
        .map(values=>makeRow(page.page, values)))
      const first = result.pages[0]
      const initial = candidates.length ? candidates : [makeRow(first.page, first.rows[0])]
      setDoc(result)
      setRows(result.approved ? [] : initial)
      setActive(0)
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
    setIssues([])
  }

  async function approve() {
    setBusy(true)
    setMessage('')
    setIssues([])
    try {
      const url = `${prefix}/${docId}/approve`
      const response = await request(url, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(rows),
      }, [422])
      const result = await response.json()
      if (!response.ok) {
        const errors:ReviewIssue[] = result.issues ?? []
        setIssues(errors)
        if (errors.length) selectRow(errors[0].position-1)
        setMessage(result.detail ?? `승인을 차단하는 오류 ${errors.length}개를 확인하세요.`)
        return
      }
      setRows([])
      setDoc(undefined)
      dialog.current?.close()
      await onSaved(result.imported)
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
    dragDepth.current = 0
    setDragging(false)
  }

  const verified = rows.length > 0 && rows.every(item=>item.verified_usd && item.verified_kg)
  const orderedFields = ['number','line','version','hs','usd','kg','declared','shipped','accepted',
    ...Object.keys(fields).filter(key=>!['number','line','version','hs','usd','kg','declared','shipped','accepted','fob','maker_state'].includes(key))]
  return <>
    <button type="button" disabled={disabled} onClick={()=>dialog.current?.showModal()}>OCR 입력</button>
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
        void upload(event.dataTransfer.files)
      }}
      onCancel={event=>{event.preventDefault(); close()}}>
      <header className="toolbar">
        <h2 id="ocr-title">OCR 입력</h2>
        <span className="toolbar-spacer"/>
        <button type="button" disabled={busy} onClick={close}>닫기</button>
      </header>
      <div className={`ocr-upload${dragging && !busy ? ' ocr-upload-active' : ''}`}>
        <p>수출신고필증 PDF를 여기에 끌어다 놓으세요.</p>
        <button type="button" autoFocus disabled={busy} onClick={()=>fileInput.current?.click()}>파일 찾기</button>
        <input ref={fileInput} type="file" hidden aria-label="수출신고필증 PDF" accept=".pdf,application/pdf" disabled={busy}
          onChange={event=>{
            const files = event.currentTarget.files
            if (files) void upload(files)
            event.currentTarget.value = ''
          }}/>
      </div>
      <p>전자 PDF와 종이 신고필증을 스캔한 PDF를 올릴 수 있습니다. 사진이나 JPG·PNG 같은 이미지 파일은 PDF로 변환한 뒤 올리세요. 정확한 추출을 위해 <strong>UNI-PASS 전자 PDF</strong>를 권장합니다.</p>
      <p>등록하기 전에 <strong>모든 추출값을 원본과 반드시 대조</strong>하고, 빠지거나 잘못 읽힌 값은 수정하세요. 금액과 순중량의 원본 확인란을 체크한 뒤 승인하세요.</p>
      {busy && <p role="status">PDF 또는 승인 요청을 처리하고 있습니다…</p>}
      {message && <p role="alert">{message}</p>}
      {doc && <div className="ocr-panels">
        <section aria-label="원본 PDF">
          <div className="toolbar">
            <button aria-label="이전 쪽" disabled={pageNumber<=1} onClick={()=>setPageNumber(pageNumber-1)}>이전</button>
            <span>{pageNumber} / {doc.pages.length}쪽</span>
            <button aria-label="다음 쪽" disabled={pageNumber>=doc.pages.length} onClick={()=>setPageNumber(pageNumber+1)}>다음</button>
            <label>확대 <select value={zoom} onChange={e=>setZoom(Number(e.target.value))}>
              {[75,100,125,150,200].map(value=><option key={value} value={value}>{value}%</option>)}
            </select></label>
          </div>
          <div className="ocr-page">
            {image ? <img alt={`수출신고필증 원본 ${pageNumber}쪽`} src={image} style={{width:`${zoom}%`}}/>
              : <p role={imageError?'alert':'status'}>{imageError || '원본을 불러오는 중…'}</p>}
          </div>
          {page?.warning && <p role="status">{page.warning}</p>}
          {!!page?.text && <details><summary>추출 문자 보기 · {page.method}</summary><pre>{page.text}</pre></details>}
        </section>
        {!doc.approved && <section aria-label="품목란 검토">
          <div className="toolbar">
            <label>검토 품목란 <select value={active} disabled={busy} onChange={e=>selectRow(Number(e.target.value))}>
              {rows.map((item,index)=><option key={index} value={index}>
                {index+1}. {item.page}쪽 · {item.values.line || '품목란 번호 미입력'}
              </option>)}
            </select></label>
            <button disabled={busy || rows.length>=300} onClick={()=>{
              setActive(rows.length)
              const header = page?.rows.length===1 ? page.rows[0] : undefined
              setRows([...rows,makeRow(pageNumber,header?.line?{}:header)])
              setIssues([])
            }}>현재 쪽 품목란 추가</button>
            <button disabled={busy || rows.length<=1} onClick={()=>{
              const next = rows.filter((_,index)=>index!==active)
              const index = Math.min(active,next.length-1)
              setRows(next)
              setActive(index)
              setPageNumber(next[index].page)
              setIssues([])
            }}>현재 품목란 제외</button>
          </div>
          {row && <fieldset disabled={busy}>
            <legend>{row.page}쪽 품목란 입력</legend>
            <div className="ocr-fields">
              {orderedFields.map(key=>{
                const problems = currentIssues.filter(issue=>issue.field===key)
                const label = key==='usd'?'품목란 FOB(USD)':fields[key]
                const id = `ocr-${key}`
                const value = row.values[key] ?? ''
                return <div key={key}>
                  {key==='maker' && <ProducerSelect members={members} onSelect={member=>
                    update({maker:String(member.number),maker_name:String(member.name),maker_state:''})}/>}
                  <label htmlFor={id}>{label}</label>
                  {choices[key] ? <select id={id} value={value} onChange={e=>update({[key]:e.target.value})}
                    aria-invalid={!!problems.length}>
                    <option value="">{key.endsWith('_state')?'회원 명부 대조':'선택'}</option>
                    {choices[key].map(option=><option key={option}>{option}</option>)}
                  </select> : <input id={id} value={value} aria-invalid={!!problems.length}
                    onChange={e=>update(key==='usd'?{usd:e.target.value,fob:e.target.value}:
                      key==='maker'||key==='maker_name'?{[key]:e.target.value,maker_state:''}:{[key]:e.target.value})}/>}
                  {numericFields.map(field=>field===key && <label className="ocr-confirm" key={field}>
                    <input type="checkbox" checked={!!row[`verified_${field}`]} disabled={!value.trim()}
                      onChange={event=>{
                        const checked = event.target.checked
                        setRows(current=>current.map((item,index)=>index===active ?
                          {...item,[`verified_${field}`]:checked ? value : ''} : item))
                        setIssues([])
                      }}/> {label} 원본 확인
                  </label>)}
                  {problems.map((problem,index)=><small key={index}>{problem.message}</small>)}
                </div>
              })}
            </div>
          </fieldset>}
          <footer className="toolbar">
            <span>{rows.filter(item=>item.verified_usd && item.verified_kg).length} / {rows.length}개 품목란 수치 확인</span>
            <button className="primary" disabled={busy || !verified} onClick={()=>void approve()}>승인하고 원장에 등록</button>
          </footer>
        </section>}
      </div>}
    </dialog>
  </>
}
