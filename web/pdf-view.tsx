import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { download, get, request } from './api'

export type PdfSource = {file:number; name:string; page:number}

export function PdfView({org, pdf}: {org:number; pdf:PdfSource}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [open,setOpen] = useState(false)
  useEffect(()=>{
    if (open && !dialog.current?.open) dialog.current?.showModal()
  }, [open])
  return <>
    <button type="button" onClick={()=>setOpen(true)}>PDF 보기</button>
    <dialog ref={dialog} className="ocr-dialog stored-pdf-dialog" aria-label="수출신고필증 원본 PDF"
      onClose={()=>setOpen(false)}>
      {open && <PdfPreview org={org} pdf={pdf} onClose={()=>dialog.current?.close()}/>}
    </dialog>
  </>
}

export function PdfPreview({org,pdf,line,onClose}: {
  org:number; pdf:PdfSource; line?:string; onClose:()=>void
}) {
  const [page,setPage] = useState(pdf.page)
  const [zoom,setZoom] = useState('fit')
  const viewport = useRef<HTMLDivElement>(null)
  const [image,setImage] = useState('')
  const [error,setError] = useState('')
  const [downloadError,setDownloadError] = useState('')
  const path = `org/${org}/ocr/${pdf.file}`
  const pages = useQuery({queryKey:[path,'pages'],queryFn:()=>get<number>(path+'/pages')})

  useEffect(()=>{
    const controller = new AbortController()
    let url = ''
    void request(`${path}/page/${page}`, {signal:controller.signal})
      .then(response=>response.blob()).then(blob=>{
        if (controller.signal.aborted) return
        url = URL.createObjectURL(blob)
        setImage(url)
      }).catch(error=>{
        if (!controller.signal.aborted) setError((error as Error).message)
      })
    return ()=>{
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [page,path])

  function showPage(next:number) {
    setImage('')
    setError('')
    setPage(next)
    viewport.current?.scrollTo(0,0)
  }

  return <section className="stored-pdf-preview" aria-label="원본 PDF 미리보기">
      <header className="toolbar">
        <h2>수출신고필증 원본 PDF</h2>
        <span className="toolbar-spacer"/>
        <button type="button" onClick={onClose}>{line===undefined ? '닫기' : '원장 목록으로 돌아가기'}</button>
      </header>
      <p>{pdf.name}</p>
      {line!==undefined && <p className="muted">품목란 {line || '미입력'} · 연결된 원본 {pdf.page}쪽</p>}
      <div className="toolbar">
        <button type="button" disabled={page<=1} onClick={()=>showPage(page-1)}>이전 쪽</button>
        <span>{page}{pages.data ? ` / ${pages.data}` : ''}쪽</span>
        <button type="button" disabled={page>=(pages.data??page)} onClick={()=>showPage(page+1)}>다음 쪽</button>
        {page!==pdf.page && <button type="button" onClick={()=>showPage(pdf.page)}>연결된 원본 쪽</button>}
        <label>확대 <select value={zoom} onChange={event=>setZoom(event.target.value)}>
          <option value="fit">너비 맞춤</option>
          {[75,100,125,150,200].map(scale=><option key={scale} value={scale}>{scale}%</option>)}
        </select></label>
        <button type="button" onClick={()=>{
          setDownloadError('')
          void download(path+'/original').catch(error=>setDownloadError((error as Error).message))
        }}>원본 PDF 다운로드</button>
      </div>
      {downloadError && <p role="alert">{downloadError}</p>}
      {pages.isError && <p role="alert">{pages.error.message}</p>}
      <div className="ocr-page" ref={viewport} tabIndex={0} aria-label="원본 PDF 페이지">
        {image ? <img src={image} style={{width:zoom==='fit' ? '100%' : zoom+'%'}} alt={`수출신고필증 원본 ${page}쪽`}/>
          : <p role={error?'alert':'status'}>{error || '원본을 불러오는 중…'}</p>}
      </div>
  </section>
}
