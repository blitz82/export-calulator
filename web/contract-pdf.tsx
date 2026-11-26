import {useEffect,useState} from 'react'
import {download,request} from './api'
import type {ContractDocument} from './contracts-model'

export function ContractPdf({org,document,page,onPage}:{org:number;document:ContractDocument;page:number;onPage:(page:number)=>void}){
  const [image,setImage]=useState(''),[error,setError]=useState(''),[zoom,setZoom]=useState('100')
  const path=`org/${org}/contracts/documents/${document.id}`
  useEffect(()=>{
    const controller=new AbortController();let url=''
    setImage('');setError('')
    void request(`${path}/page/${page}`,{signal:controller.signal}).then(r=>r.blob()).then(blob=>{
      if(controller.signal.aborted)return
      url=URL.createObjectURL(blob);setImage(url)
    }).catch(e=>{if(!controller.signal.aborted)setError((e as Error).message)})
    return ()=>{controller.abort();if(url)URL.revokeObjectURL(url)}
  },[path,page])
  return <section className="panel contract-pdf" aria-label="계약 PDF 원본">
    <h2>계약 PDF 원본</h2><p>{document.name} · {document.pages}쪽</p>
    <div className="toolbar"><button type="button" disabled={page<=1} onClick={()=>onPage(page-1)}>이전 쪽</button>
      <label>원본 페이지<input type="number" min="1" max={document.pages} value={page} onChange={e=>{const value=Number(e.target.value);if(value>=1&&value<=document.pages)onPage(value)}}/></label>
      <button type="button" disabled={page>=document.pages} onClick={()=>onPage(page+1)}>다음 쪽</button>
      <label>PDF 확대<select value={zoom} onChange={e=>setZoom(e.target.value)}>{[75,100,125,150,200].map(n=><option key={n} value={n}>{n}%</option>)}</select></label>
      <button type="button" onClick={()=>void download(path+'/original').catch(e=>setError((e as Error).message))}>원본 다운로드</button>
    </div>
    <div className="contract-pdf-viewport" tabIndex={0} aria-label="계약 원본 페이지">
      {image?<img src={image} alt={`계약 원본 ${page}쪽`} style={{width:zoom+'%'}}/>:<p role={error?'alert':'status'}>{error||'PDF를 불러오는 중…'}</p>}
    </div>
    {error&&image&&<p role="alert">{error}</p>}
    <details><summary>원본 식별 정보</summary><p>등록: {document.created}</p><p className="contract-hash">SHA-256: {document.sha}</p></details>
  </section>
}
