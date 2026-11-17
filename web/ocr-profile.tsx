import { useState } from 'react'
import { get, request } from './api'

export type Box = [number, number, number, number]
type Region = {field:string; box:Box; anchor?:string; format?:string}
type Profile = {name:string; variant:string; regions:Region[]}
type Version = {id:number; data:Profile; active:number; validated:number}
export type Preview = Record<string,{normalized:string;box:Box}>
const options:Record<string,string> = {number:'신고번호',declared:'신고일자',accepted:'수리일자',shipped:'선적일자',hs:'HS코드'}
const blank = JSON.stringify({name:'',variant:'preloading-cover',regions:[]},null,2)

export function OcrProfilePanel({prefix,file,page,selection,onPreview}: {
  prefix:string; file:number; page:number; selection?:Box; onPreview:(preview:Preview)=>void
}) {
  const [versions,setVersions] = useState<Version[]>([])
  const [selected,setSelected] = useState(0)
  const [draft,setDraft] = useState(blank)
  const [field,setField] = useState('number')
  const [preview,setPreview] = useState<{page:number;fields:Preview}>()
  const [status,setStatus] = useState('')
  const [busy,setBusy] = useState(false)
  const path = prefix + '/profiles'
  const saved = versions.find(item=>item.id===selected)

  async function run(action:()=>Promise<void>) {
    setBusy(true)
    setStatus('')
    try { await action() } catch (error) { setStatus((error as Error).message) }
    finally { setBusy(false) }
  }

  function edit(value:string) {
    setDraft(value)
    setSelected(0)
    setPreview(undefined)
    onPreview({})
  }

  async function refresh() { setVersions(await get<Version[]>(path)) }

  return <details onToggle={event=>{if(event.currentTarget.open) void run(refresh)}}>
    <summary>고급 OCR 템플릿 설정</summary>
    <fieldset disabled={busy}>
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
        await request(`${path}/0/activate`,{method:'POST'})
        await refresh()
        setStatus('기본 템플릿을 복원했습니다.')
      })}>기본 템플릿 복원</button>
      <p>원본 영역을 지정하고 필드에 추가하세요. JSON의 format으로 날짜 또는 HS코드의 고정 형식을 선택할 수 있습니다.</p>
      <label>영역 필드 <select value={field} onChange={event=>setField(event.target.value)}>
        {Object.entries(options).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
      <button type="button" disabled={!selection} onClick={()=>void run(async()=>{
        const profile:Profile = JSON.parse(draft)
        profile.regions = [...profile.regions.filter(region=>region.field!==field), {field,box:selection!}]
        edit(JSON.stringify(profile,null,2))
      })}>선택 영역 추가</button>
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
          await request(`${path}/${selected}/activate`,{method:'POST'})
          await refresh()
          setStatus('활성화했습니다. 미승인 PDF를 다시 업로드하면 적용됩니다.')
        })}>이 버전 활성화</button>
      </div>
      {preview?.page===page && !!Object.keys(preview.fields).length && <ul>{Object.entries(preview.fields).map(([key,candidate])=>
        <li key={key}>{options[key]} {candidate.normalized}</li>)}</ul>}
      {status && <p role="status">{status}</p>}
    </fieldset>
  </details>
}
