import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { download, get, request } from './api'

export type PdfSource = {file:number; name:string; page:number}

export function PdfView({org, pdf}: {org:number; pdf:PdfSource}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [open,setOpen] = useState(false)
  const [page,setPage] = useState(pdf.page)
  const [image,setImage] = useState('')
  const [error,setError] = useState('')
  const [downloadError,setDownloadError] = useState('')
  const path = `org/${org}/ocr/${pdf.file}`
  const pages = useQuery({queryKey:[path,'pages'],queryFn:()=>get<number>(path+'/pages'),enabled:open})

  useEffect(()=>{
    if (!open) return
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
  }, [open,page,path])

  function showPage(next:number) {
    setImage('')
    setError('')
    setPage(next)
  }

  return <>
    <button type="button" onClick={()=>{
      showPage(pdf.page)
      setDownloadError('')
      setOpen(true)
      dialog.current?.showModal()
    }}>PDF 보기</button>
    <dialog ref={dialog} className="ocr-dialog stored-pdf-dialog" aria-label="수출신고필증 원본 PDF"
      onClose={()=>setOpen(false)}>
      <header className="toolbar">
        <h2>수출신고필증 원본 PDF</h2>
        <span className="toolbar-spacer"/>
        <button type="button" onClick={()=>dialog.current?.close()}>닫기</button>
      </header>
      <p>{pdf.name}</p>
      <div className="toolbar">
        <button type="button" disabled={page<=1} onClick={()=>showPage(page-1)}>이전 쪽</button>
        <span>{page}{pages.data ? ` / ${pages.data}` : ''}쪽</span>
        <button type="button" disabled={page>=(pages.data??page)} onClick={()=>showPage(page+1)}>다음 쪽</button>
        <button type="button" onClick={()=>{
          setDownloadError('')
          void download(path+'/original').catch(error=>setDownloadError((error as Error).message))
        }}>원본 PDF 다운로드</button>
      </div>
      {downloadError && <p role="alert">{downloadError}</p>}
      {pages.isError && <p role="alert">{pages.error.message}</p>}
      <div className="ocr-page">
        {image ? <img src={image} alt={`수출신고필증 원본 ${page}쪽`}/>
          : <p role={error?'alert':'status'}>{error || '원본을 불러오는 중…'}</p>}
      </div>
    </dialog>
  </>
}
