import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import { AllCommunityModule, ModuleRegistry, themeQuartz } from 'ag-grid-community'
import type { CellClassParams, ColDef, GridApi, ITooltipParams } from 'ag-grid-community'
import { download, get, request, send } from './api'
import { useSearchParams } from 'react-router-dom'
import { dateInputHelp, HelpTip, ledgerHelp } from './help-tip'

ModuleRegistry.registerModules([AllCommunityModule])
const theme = themeQuartz.withParams({accentColor:'#222', fontFamily:'inherit', fontSize:12,
  headerBackgroundColor:'#ededed', borderColor:'#d5d5d5', spacing:5, wrapperBorderRadius:0})

export type Fields = Record<string, string>
export type Row = {id?: number; revision?: number; source?: number | null; [key: string]: unknown}
export type Problem = {code: string; field: string; message: string; severity: string; row_id?: number}
type Result = {row?: Row; issues: Problem[]}
type Kind = 'members' | 'lines'
const options: Record<string, string[]> = {
  kind:['생산자','수출업체','복수'], scope:['대상','제외','미확정'], status:['유효','취소','미확정'],
}
const roleKeys = new Set(['agent','owner','maker'])
const normalizeNumber = (value:unknown) => String(value??'').normalize('NFKC').replace(/[\s-]/g,'')
const normalizeCompany = (value:unknown) => String(value??'').normalize('NFKC').replace(/\s+|주식회사|\(주\)/g,'').toLowerCase()
const memberNames = (row:Row) => [row.name,...String(row.aliases??'').split('|')].map(normalizeCompany).filter(Boolean)

function nameRank(query:string, name:string) {
  if (query===name) return 0
  if (name.includes(query)) return 1
  if (query.length<3 || Math.abs(query.length-name.length)>1) return Infinity
  const [shorter,longer]=query.length<=name.length?[query,name]:[name,query]
  const first=[...shorter].findIndex((letter,index)=>letter!==longer[index])
  if (first===-1) return 2
  const offset=shorter.length===longer.length?1:0
  return shorter.slice(first+offset)===longer.slice(first+1)?2:Infinity
}
const visibleError = (message:string) => message.replace(/^[MD]\d{3}\s+/, '')
const defaultLineFields = ['number','line','usd','hs','shipped','declared','agent_name','owner_name','maker_name']
const internalLineFields = new Set(['line','version'])
const newLineDefaults = {line:'1',version:'1',scope:'대상',status:'유효'}
const extraLineFields = new Set(['accepted','country','product','fob','kg','quantity','unit'])
const emptyChecks: Record<string,Problem[]> = {}
const emptyRows: Row[] = []
const numeric = new Set(['line','version','usd','fob','kg','quantity'])

export function LedgerGrid({org, kind, fields}: {org: number; kind: Kind; fields: Fields}) {
  const path = `org/${org}/data/${kind}`
  const query = useQuery({queryKey:[path], queryFn:() => get<Row[]>(path)})
  const checksPath = `org/${org}/line-checks`
  const checks = useQuery({queryKey:[checksPath],queryFn:()=>get<Record<string,Problem[]>>(checksPath),enabled:kind==='lines'})
  const [params,setParams] = useSearchParams()
  const problemOnly = params.get('filter') === 'issues'
  const rowChecks = checks.data ?? emptyChecks
  const rows = query.data ?? emptyRows
  const members = useQuery({queryKey:[`org/${org}/data/members`], queryFn:() => get<Row[]>(`org/${org}/data/members`), enabled:kind==='lines'})
  const [selected, setSelected] = useState<Row>()
  const [draft, setDraft] = useState<Row>({})
  const [issues, setIssues] = useState<Problem[]>([])
  const [search, setSearch] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [hidden, setHidden] = useState<string[]>(() => kind==='lines' ? Object.keys(fields).filter(key=>!defaultLineFields.includes(key)) : [])
  const [showExtras, setShowExtras] = useState(false)
  const [resetText, setResetText] = useState('')
  const [issueRows, setIssueRows] = useState<{row: number; issues: Problem[]}[]>([])
  const validation = useRef(0)
  const grid = useRef<GridApi<Row> | null>(null)
  const nextIssue = useRef(0)
  const form = useRef<HTMLFormElement>(null)
  const excelFile = useRef<HTMLInputElement>(null)
  const resetDialog = useRef<HTMLDialogElement>(null)
  const dirty = selected && Object.keys(fields).some(key => String(selected[key] ?? '') !== String(draft[key] ?? ''))
  const visibleRows = useMemo(() => problemOnly ? rows.filter(row=>rowChecks[String(row.id)]?.some(p=>p.severity!=='INFO')) : rows,
    [rows, problemOnly, rowChecks])
  useEffect(() => {
    if (!dirty) return
    const protect = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload',protect)
    return () => window.removeEventListener('beforeunload',protect)
  }, [dirty])
  const columns = useMemo<ColDef<Row>[]>(() => [
    ...(kind==='lines' ? [...defaultLineFields,...Object.keys(fields).filter(key=>!defaultLineFields.includes(key))]
      : Object.keys(fields)).map(key => ({
      field:key, headerName:key==='line'?'품목란':fields[key], minWidth:key === 'name' ? 195 : 90,
      width:key==='line'?115:key==='number'?165:key==='product'?165:130,
      valueFormatter:(p:{value:unknown})=>numeric.has(key)
        ? String(p.value??'').replace(/^-?\d+/, value=>value.replace(/\B(?=(\d{3})+(?!\d))/g,',')) : String(p.value??''),
      hide:hidden.includes(key), pinned:['number','line'].includes(key) ? 'left' as const : undefined,
      cellClassRules: {
        'cell-error': (p:CellClassParams<Row>) => !!rowChecks[String(p.data?.id)]?.some(item=>item.field===key&&item.severity==='ERROR'),
        'cell-warning': (p:CellClassParams<Row>) => !!rowChecks[String(p.data?.id)]?.some(item=>item.field===key&&item.severity==='WARNING'),
        'numeric': () => numeric.has(key),
      },
      tooltipValueGetter:(p:ITooltipParams<Row>)=>rowChecks[String(p.data?.id)]?.filter(item=>item.field===key&&item.severity!=='INFO').map(item=>item.message).join('\n'),
    })),
    ...(kind==='lines' ? [{
      colId:'validation',headerName:'입력 검증',width:125,pinned:'right' as const, cellClass:'validation-cell', headerClass:'validation-header',
      valueGetter: (p: {data?: Row}) => {
        if (checks.isPending || checks.isError) return '—'
        const problems = rowChecks[String(p.data?.id)] ?? []
        return problems.some(item=>item.severity==='ERROR')?'확정 차단':problems.some(item=>item.severity==='WARNING')?'확인 필요':'통과'
      },
      cellClassRules:{'cell-error':(p: {value:unknown})=>p.value==='확정 차단','cell-warning':(p: {value:unknown})=>p.value==='확인 필요'},
    }] : []),
  ], [fields, hidden, kind, rowChecks, checks.isPending, checks.isError])
  useEffect(() => {
    grid.current?.refreshCells({columns:['validation'],force:true})
  }, [rowChecks, checks.isPending, checks.isError])
  const values = (row: Row) => Object.fromEntries(Object.keys(fields).map(key => [key, String(row[key] ?? '')]))
  const validate = async (row: Row) => {
    const revision = ++validation.current
    try {
      const next = await send<Problem[]>(path + '/validate', {id:row.id, values:values(row)})
      if (revision === validation.current) setIssues(next)
    } catch (error) {
      if (revision === validation.current) setMessage((error as Error).message)
    }
  }
  const select = (row?: Row) => {
    if (dirty && !window.confirm('저장하지 않은 변경사항을 취소하시겠습니까?')) return
    const initial = row ?? (kind==='lines' ? newLineDefaults : {})
    nextIssue.current = 0
    setSelected(initial)
    setDraft({...initial})
    setIssues(rowChecks[String(row?.id)] ?? [])
    void validate(initial)
  }
  const action = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage('')
    try { await fn() } catch (error) { setMessage(visibleError((error as Error).message)) }
    finally { setBusy(false) }
  }
  const focus = (field: string) => {
    const api = grid.current
    const rowIndex = draft.id ? api?.getRowNode(String(draft.id))?.rowIndex : undefined
    if (api && rowIndex != null && api.getColumn(field)) {
      if (hidden.includes(field)) setHidden(current=>current.filter(key=>key!==field))
      api.ensureIndexVisible(rowIndex)
      requestAnimationFrame(() => api.ensureColumnVisible(field))
    }
    if (extraLineFields.has(field) && !showExtras) {
      setShowExtras(true)
      requestAnimationFrame(() => form.current?.querySelector<HTMLElement>(`[name="${field}"]`)?.focus())
    } else {
      form.current?.querySelector<HTMLElement>(`[name="${field}"]`)?.focus()
    }
  }
  const reload = async () => {
    await Promise.all([query.refetch(), ...(kind==='lines' ? [checks.refetch()] : [])])
  }
  const save = async () => {
    const [result] = await send<Result[]>(path, [{id:draft.id, revision:draft.revision, values:values(draft)}])
    ++validation.current
    setIssues(result.issues)
    if (!result.row) { setMessage('오류 항목을 수정한 후 저장하세요.'); return }
    setDraft(result.row)
    setSelected(result.row)
    setMessage('저장됨 · ' + new Date().toLocaleTimeString('ko-KR'))
    await reload()
  }
  const upload = async (file: File) => {
    const params = new URLSearchParams({name:file.name})
    const response = await request(`org/${org}/import/${kind}?${params}`, {method:'POST', body:file})
    const result = await response.json()
    setIssueRows(result.rejected)
    setMessage(`${result.profile} 양식 · ${result.imported}행 저장 · ${result.rejected.length}행 보완 필요`)
    await reload()
  }
  return <section className="ledger-workspace">
    <div className="toolbar ledger-tools">
      <label>자료 검색<input type="search" placeholder={kind==='lines'?'신고번호 또는 업체명':'업체명 또는 사업자번호'} value={search} onChange={e => setSearch(e.target.value)}/></label>
      {kind==='lines' && <button aria-pressed={problemOnly} onClick={()=>setParams(problemOnly?{}:{filter:'issues'})}>보완 필요만</button>}
      <div className="toolbar-spacer"/>
      <details className="excel-menu"><summary className="button">Excel 가져오기·내보내기</summary>
        <div className="menu-items" onClick={event=>event.currentTarget.parentElement?.removeAttribute('open')}>
        <button disabled={busy} onClick={()=>excelFile.current?.click()}>Excel 업로드</button>
        <button disabled={busy} onClick={() => action(async () => { await download(`org/${org}/export/${kind}`); setMessage('다운로드 완료') })}>Excel 다운로드</button>
        <button disabled={busy} onClick={() => action(async () => { await download(`org/${org}/export/${kind}?template=true`); setMessage('표준 양식 다운로드 완료') })}>표준 양식</button>
        </div>
      </details>
      <input ref={excelFile} hidden type="file" accept=".xlsx" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; e.target.value=''; if (file) void action(() => upload(file))
      }}/>
      <button className={selected?undefined:"primary"} disabled={busy} onClick={() => select()}>{kind==='lines'?'신고필증 추가':'회원사 추가'}</button>
      {kind==='lines' && <button className="danger" disabled={busy || !rows.length} onClick={()=>{
        setResetText('')
        resetDialog.current?.showModal()
      }}>전체 삭제</button>}
    </div>
    {kind==='lines' && <dialog ref={resetDialog} className="reset-dialog" aria-labelledby="reset-title"
      onCancel={event=>{if (busy) event.preventDefault()}}>
      <form onSubmit={event=>{
        event.preventDefault()
        if (resetText!=='모두리셋' || busy) return
        void action(async()=>{
          const result=await send<{count:number}>(path+'/reset',{confirmation:resetText})
          ++validation.current
          setSelected(undefined)
          setDraft({})
          setIssues([])
          setIssueRows([])
          resetDialog.current?.close()
          setMessage(`원장 ${result.count.toLocaleString('ko-KR')}행을 비웠습니다.`)
          await reload()
        })
      }}>
        <h2 id="reset-title">원장 전체 리셋</h2>
        <p>선택한 품목의 원장 전체 {rows.length.toLocaleString('ko-KR')}행을 비웁니다. 원본 파일, 계산 이력 및 확정본은 보존됩니다.</p>
        <label><span>확인 문구 <strong>모두리셋</strong> <HelpTip text="정확히 입력하면 선택한 품목의 원장 전체를 비울 수 있습니다."/></span>
          <input aria-label="리셋 확인 문구" autoComplete="off" value={resetText} disabled={busy} onChange={event=>setResetText(event.target.value)}/>
        </label>
        <p role="alert">{message}</p>
        <div className="toolbar">
          <button type="button" disabled={busy} onClick={()=>resetDialog.current?.close()}>취소</button>
          <button type="submit" className="danger danger-confirm" disabled={busy||resetText!=='모두리셋'}>{busy?'처리 중…':'모든 행 비우기'}</button>
        </div>
      </form>
    </dialog>}
    <details className="compact"><summary>열 표시</summary>
      <div className="checklist">{Object.entries(fields).map(([key,label]) => <label key={key}>
        <input type="checkbox" checked={!hidden.includes(key)} onChange={e => setHidden(e.target.checked ? hidden.filter(k => k!==key) : [...hidden,key])}/>
        {label}</label>)}</div>
    </details>
    <div className="ledger-status"><span>{visibleRows.length.toLocaleString('ko-KR')}행</span>
      <span role="status">{busy ? '처리 중…' : dirty ? '저장 전 변경사항' : message || (query.isError || checks.isError ? '자료를 불러오지 못했습니다.' : '행을 선택하여 수정할 수 있습니다.')}</span>
    </div>
    {!!issueRows.length && <details className="compact"><summary>일괄 입력 검증 · {issueRows.length}행</summary>
      {issueRows.slice(0,200).map(row => <p key={row.row}>{row.row}행 <HelpTip text={row.issues.map(p=>p.message).join(' / ')}/></p>)}
    </details>}
    <div className="ledger-split"><div className="grid-shell ledger-grid"><AgGridReact<Row> theme={theme} onGridReady={event => {grid.current=event.api}}
      rowData={visibleRows} columnDefs={columns} quickFilterText={search} loading={query.isLoading || (problemOnly && checks.isLoading)}
      tooltipShowDelay={0} tooltipHideDelay={0}
      getRowId={p => String(p.data.id)} defaultColDef={{sortable:true,resizable:true,filter:false,suppressHeaderMenuButton:true,suppressHeaderFilterButton:true}} onRowClicked={e => {if (!busy) select(e.data)}}
      rowClassRules={{'selected-ledger-row':p=>!!draft.id&&p.data?.id===draft.id}}
      onCellKeyDown={e => {if (!busy && (e.event as KeyboardEvent)?.key === 'Enter') select(e.data)}}
      overlayNoRowsTemplate="등록된 자료가 없습니다."/></div>
    {selected && <form ref={form} className="ledger-editor" onSubmit={e => {e.preventDefault(); void action(save)}}>
      <div className="section-top"><h2>{draft.id ? `행 수정 · ${draft.id}` : '새 행 입력'}</h2>
        <button type="button" disabled={busy} onClick={() => {
          if (dirty && !window.confirm('저장하지 않은 변경사항을 취소하시겠습니까?')) return
          ++validation.current; setSelected(undefined)
        }}>닫기</button></div>
      <div className="editor-scroll">
      {issues.some(p=>p.severity!=='INFO') && <div className="issue-list" aria-label="선택 행 검증 결과">
        <div className="section-top"><b>확인 필요 {new Set(issues.filter(p=>p.severity!=='INFO').map(p=>p.field)).size}개</b><button type="button" onClick={() => {
          const items = issues.filter(p=>p.severity!=='INFO')
          focus(items[nextIssue.current % items.length].field)
          nextIssue.current += 1
        }}>다음 오류로 이동</button></div>
      </div>}
      {kind==='lines' && <p className="muted">실적 연도 정책이 지정한 날짜를 입력하세요. 순중량 등 참고 항목은 선택입니다.</p>}
      <fieldset disabled={busy} className="entry">
        {(() => {
          const renderField = ([key,label]: [string,string]) => {
          const problems = issues.filter(p => p.field === key && p.severity!=='INFO')
          const role=key.split('_')[0]
          const stateKey=role+'_state'
          const roleField=kind==='lines'&&roleKeys.has(role)
          const number=normalizeNumber(draft[role])
          const roleName=String(draft[role+'_name']??'').trim()
          const member=roleField&&members.data?.find(row=>normalizeNumber(row.number)===number)
          const state=String(draft[stateKey]??'')
          const matches=member&&!!normalizeCompany(roleName)&&
            memberNames(member).includes(normalizeCompany(roleName))
          const stateOptions=!number&&!roleName?['원본에 없음']:
            members.isSuccess&&!member&&/^\d{10}$/.test(number)&&roleName?['비회원 확인']:[]
          const choices=key===stateKey&&roleField
            ? [...new Set([...stateOptions,...(state&&state!=='회원 대조'?[state]:[])])]
            : options[key]
          const change = (value: string) => {
            const next = {...draft,[key]:value}
            if (roleField && key!==stateKey) next[stateKey]=''
            setDraft(next)
            void validate(next)
          }
          const memberName = /^(agent|owner|maker)_name$/.test(key)
          const queryName = memberName ? normalizeCompany(roleName) : ''
          const candidates = queryName && !matches ? (members.data??[])
            .map(row=>({row,rank:Math.min(...memberNames(row).map(name=>nameRank(queryName,name)))}))
            .filter(({row,rank})=>/^\d{10}$/.test(normalizeNumber(row.number))&&Number.isFinite(rank))
            .sort((a,b)=>a.rank-b.rank)
            .slice(0,5) : []
          const inputId=`ledger-${kind}-${key}`
          if (roleField&&key===stateKey&&!choices?.length) {
            const hasIssue=issues.some(p=>p.severity!=='INFO'&&[role,role+'_name',stateKey].includes(p.field))
            return matches&&!hasIssue
              ? <p className="muted" key={key}>{fields[role+'_name']} · 회원사 명부 자동 대조 완료</p>
              : null
          }
          return <div className="field-entry" key={key}><div className="field-caption"><label htmlFor={inputId}>{label}{kind==='lines'&&extraLineFields.has(key)?' (선택)':''}</label>
            <HelpTip text={ledgerHelp(key,kind)??label}/></div>
            <div className="error-anchor">
            {choices ? <select id={inputId} name={key} aria-describedby={problems.length?`${inputId}-error`:undefined} aria-invalid={!!problems.length} value={choices.includes(String(draft[key]??''))?String(draft[key]):''} onChange={e => change(e.target.value)}>
              <option value="">선택</option>{choices.map(item => <option key={item}>{item}</option>)}
            </select> : <input id={inputId} name={key} title={kind==='lines'&&(key==='declared'||key==='shipped')?dateInputHelp:undefined} aria-describedby={problems.length?`${inputId}-error`:undefined} value={String(draft[key] ?? '')}
              onChange={e => change(e.target.value)} aria-invalid={!!problems.length}/>}
            {!!problems.length&&<span className="field-error" id={`${inputId}-error`}>{problems.map(p=>p.message).join(' ')}</span>}
            </div>
            {!!candidates.length&&<ul className="member-candidates" aria-label={`${label} 회원사 후보`}>
              {candidates.map(({row})=><li key={row.id}>
                <button type="button" onClick={()=>{
                  const next={...draft,[key]:String(row.name),[role]:String(row.number),[stateKey]:''}
                  setDraft(next)
                  void validate(next)
                }}>{String(row.name)} · {String(row.number)}</button>
              </li>)}
            </ul>}
          </div>
          }
          const entries = Object.entries(fields)
          if (kind!=='lines') return entries.map(renderField)
          return <>
            {entries.filter(([key])=>!roleKeys.has(key.split('_')[0])&&!extraLineFields.has(key)&&!internalLineFields.has(key)).map(renderField)}
            {[...roleKeys].map(role=><fieldset className="party-fields" key={role}>
              <legend>{fields[role+'_name']}</legend>
              {entries.filter(([key])=>key.split('_')[0]===role).map(renderField)}
            </fieldset>)}
            {showExtras&&entries.filter(([key])=>extraLineFields.has(key)).map(renderField)}
          </>
        })()}
      </fieldset>
      {kind==='lines' && <button className="extra-toggle" type="button" aria-expanded={showExtras} onClick={()=>setShowExtras(!showExtras)}>
        {showExtras?'참고 항목 접기':'선택 참고 항목 펼치기'}
      </button>}
      </div><div className="toolbar editor-actions">
        <button type="submit" className="primary" disabled={busy}>저장</button>
        {kind==='lines' && draft.id && <button type="button" className="danger" disabled={busy} onClick={() => {
          const reason = window.prompt('원장 행을 제외합니다. 제외 사유를 입력하세요.')
          if (reason?.trim()) void action(async () => {
            await request(`${path}/${draft.id}?revision=${draft.revision}&reason=${encodeURIComponent(reason)}`, {method:'DELETE'})
            setSelected(undefined); await reload(); setMessage('원장 행 제외 완료')
          })
        }}>행 제외</button>}
      </div>
    </form>}
    </div>
  </section>
}
