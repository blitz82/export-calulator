import { useEffect, useState } from 'react'
import { get, request } from './api'

export type Box = [number, number, number, number]
type Region = {field:string; box:Box; anchor?:string; format?:string}
type Profile = {name:string; variant:string; source?:'text'|'scan'; regions:Region[]}
type Version = {id:number; data:Profile; active:number; validated:number}
export type Preview = Record<string,{normalized:string;box:Box}>
export const ocrFields:Record<string,string> = {
  number:'신고번호',line:'실제 품목란번호',version:'정정 버전',declared:'신고일자',accepted:'수리일자',shipped:'선적일자',
  hs:'HS코드',country:'수출국',product:'품명',usd:'품목란 FOB(USD)',kg:'순중량(kg)',quantity:'수량',unit:'수량단위',
  agent_name:'수출대행자',agent:'수출대행자 사업자번호',owner_name:'수출화주',owner:'수출화주 사업자번호',
  maker_name:'제조자',maker:'제조자 사업자번호',
}
export const variants:Record<string,string> = {'preloading-cover':'적재 전 갑지','preloading-continuation':'적재 전 을지',
  'completed-cover':'수출이행 갑지','completed-continuation':'수출이행 을지'}

export function OcrProfilePanel({prefix,file,page,variant,scan,selection,reviewBusy,onBusyChange,onPreview}: {
  prefix:string; file:number; page:number; variant?:string; scan:boolean; selection?:Box
  reviewBusy:boolean
  onBusyChange:(busy:boolean)=>void
  onPreview:(preview:Preview)=>void
}) {
  const blank = JSON.stringify({name:'',variant:variant && variants[variant] ? variant : 'preloading-cover',
    source:scan ? 'scan' : 'text',regions:[]},null,2)
  const [versions,setVersions] = useState<Version[]>([])
  const [selected,setSelected] = useState(0)
  const [draft,setDraft] = useState(blank)
  const [field,setField] = useState('number')
  const [preview,setPreview] = useState<{page:number;fields:Preview}>()
  const [status,setStatus] = useState('')
  const [busy,setBusy] = useState(false)
  const path = prefix + '/profiles'
  const saved = versions.find(item=>item.id===selected)
  let config:Profile | undefined
  try {
    const parsed = JSON.parse(draft)
    if (parsed && typeof parsed==='object' && Array.isArray(parsed.regions)) config = parsed
  } catch { /* The JSON editor can hold an incomplete draft. */ }

  useEffect(()=>{
    const controller = new AbortController()
    setBusy(true)
    void request(path,{signal:controller.signal}).then(response=>response.json()).then(data=>{
      if (!controller.signal.aborted) setVersions(data)
    }).catch(error=>{
      if (!controller.signal.aborted) setStatus((error as Error).message)
    }).finally(()=>{
      if (!controller.signal.aborted) setBusy(false)
    })
    return ()=>controller.abort()
  }, [path])

  async function run(action:()=>Promise<void>) {
    onBusyChange(true)
    setStatus('')
    try { await action() } catch (error) { setStatus((error as Error).message) }
    finally { onBusyChange(false) }
  }

  function edit(value:string) {
    setDraft(value)
    setSelected(0)
    setPreview(undefined)
    onPreview({})
  }

  async function refresh() { setVersions(await get<Version[]>(path)) }

  function changeConfig(changes:Partial<Profile>) {
    if (config) edit(JSON.stringify({...config,...changes},null,2))
  }

  return <section className="ocr-profile" aria-label="OCR 서식 설정">
    <p>서식 변경은 이후 PDF를 가져올 때 적용됩니다.</p>
    <fieldset disabled={busy || reviewBusy}>
      <legend>서식별 추출 영역과 버전</legend>
      <label>저장된 버전 <select value={selected} onChange={event=>{
        const version = versions.find(item=>item.id===Number(event.target.value))
        edit(version ? JSON.stringify(version.data,null,2) : blank)
        setSelected(version?.id ?? 0)
      }}>
        <option value={0}>새 버전</option>
        {versions.map(item=><option key={item.id} value={item.id}>
          {item.id} · {item.data.name}{item.active ? ' · 활성' : item.validated ? ' · 검증 통과' : ''}
        </option>)}
      </select></label>
      <button type="button" onClick={()=>void run(async()=>{
        if (!window.confirm('기본 서식으로 복원할까요? 이후 PDF를 가져올 때 적용됩니다.')) return
        await request(`${path}/0/activate`,{method:'POST'})
        await refresh()
        setStatus('기본 템플릿을 복원했습니다.')
      })}>기본 템플릿 복원</button>
      <p>왼쪽 PDF에서 영역을 지정하고, 아래에서 입력 항목을 선택해 서식에 추가하세요.</p>
      <label>영역 필드 <select value={field} onChange={event=>setField(event.target.value)}>
        {Object.entries(ocrFields).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
      <button type="button" disabled={!selection || !config} onClick={()=>{
        if (selection && config) changeConfig({regions:[...config.regions.filter(region=>region.field!==field), {field,box:selection}]})
      }}>선택 영역 추가</button>
      <label>템플릿 이름 <input value={config?.name ?? ''} maxLength={60} disabled={!config}
        onChange={event=>changeConfig({name:event.target.value})}/></label>
      <label>대상 서식 <select value={config?.variant ?? ''} disabled={!config}
        onChange={event=>changeConfig({variant:event.target.value})}>
        {Object.entries(variants).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
      <label>인식 방식 <select value={config?.source ?? 'text'} disabled={!config}
        onChange={event=>changeConfig({source:event.target.value==='scan' ? 'scan' : 'text'})}>
        <option value="text">전자 PDF 텍스트</option>
        <option value="scan">스캔 PDF · Windows OCR</option>
      </select></label>
      <details>
        <summary>서식 백업 및 이전</summary>
        <label>템플릿 JSON<textarea rows={11} value={draft} maxLength={8192} onChange={event=>edit(event.target.value)}/></label>
        <label>JSON 가져오기 <input type="file" accept=".json,application/json" onChange={event=>{
          const imported = event.target.files?.[0]
          event.target.value = ''
          if (imported) void run(async()=>{
            if (imported.size>8192) throw new Error('템플릿 JSON은 8KB 이하로 선택하세요.')
            edit(JSON.stringify(JSON.parse(await imported.text()),null,2))
          })
        }}/></label>
        <div className="toolbar">
          <button type="button" onClick={()=>void run(async()=>{
            const value = JSON.stringify(JSON.parse(draft),null,2)
            const url = URL.createObjectURL(new Blob([value],{type:'application/json'}))
            const link = document.createElement('a')
            link.href = url
            link.download = 'epms-ocr-template.json'
            link.click()
            setTimeout(()=>URL.revokeObjectURL(url),1000)
          })}>JSON 내보내기</button>
        </div>
      </details>
      <div className="toolbar">
        <button type="button" disabled={!!saved} onClick={()=>void run(async()=>{
          const response = await request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:draft})
          const result:{id:number} = await response.json()
          await refresh()
          setSelected(result.id)
          setStatus('초안 버전을 저장했습니다.')
        })}>새 버전 저장</button>
        <button type="button" disabled={!saved} onClick={()=>void run(async()=>{
          const response = await request(`${path}/${selected}/preview?${new URLSearchParams({file_id:String(file),page:String(page)})}`,{method:'POST'})
          const result:{candidates:Preview; passed:boolean} = await response.json()
          setPreview({page,fields:result.candidates})
          onPreview(result.candidates)
          await refresh()
          setStatus(result.passed ? '추출 미리보기를 통과했습니다. 원본과 대조하세요.' : '모든 영역의 값을 확인할 수 없습니다.')
        })}>현재 쪽 미리보기</button>
        <button type="button" disabled={!saved?.validated || !!saved.active} onClick={()=>void run(async()=>{
          if (!window.confirm('이 서식을 활성화할까요? 이후 PDF를 가져올 때 적용됩니다.')) return
          await request(`${path}/${selected}/activate`,{method:'POST'})
          await refresh()
          setStatus('활성화했습니다. 미승인 PDF를 다시 업로드하면 적용됩니다.')
        })}>이 버전 활성화</button>
      </div>
      {preview?.page===page && !!Object.keys(preview.fields).length && <ul>{Object.entries(preview.fields).map(([key,candidate])=>
        <li key={key}>{ocrFields[key]} {candidate.normalized}</li>)}</ul>}
      {status && <p role="status">{status}</p>}
    </fieldset>
  </section>
}
