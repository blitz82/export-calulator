import { NavLink } from 'react-router-dom'
import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import { AllCommunityModule, ModuleRegistry, themeQuartz } from 'ag-grid-community'
import type { CellClassParams, ColDef, GridApi, ITooltipParams } from 'ag-grid-community'
import { download, get, request, send } from './api'
import { useSearchParams } from 'react-router-dom'
import { dateInputHelp, HelpTip, ledgerHelp } from './help-tip'
import { OcrInput } from './ocr-input'
import { PdfView, type PdfSource } from './pdf-view'
import { ProducerSelect } from './producer-select'

ModuleRegistry.registerModules([AllCommunityModule])
const theme = themeQuartz.withParams({accentColor:'#222', fontFamily:'inherit', fontSize:12,
  headerBackgroundColor:'#ededed', borderColor:'#d5d5d5', spacing:5, wrapperBorderRadius:0})
const memberTheme = theme.withParams({backgroundColor:'#fff', oddRowBackgroundColor:'#fff', rowHoverColor:'transparent'})

export type Fields = Record<string, string>
export type Row = {id?: number; revision?: number; source?: number | null; pdf?: PdfSource | null; [key: string]: unknown}
export type Problem = {code: string; field: string; message: string; severity: string; row_id?: number}
type Result = {row?: Row; issues: Problem[]}
type ImportIssue = {row: number; issues: Problem[]}
type ImportResult = {
  profile: string
  imported: number
  rejected: ImportIssue[]
  needs_review?: number
  duplicates?: ImportIssue[]
  skipped?: number
}
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
const isExcludedLine = (row?:Row) => row?.scope==='제외' || row?.status==='취소'
const hasMissingIdentity = (row:Row) => [...internalLineFields].some(key=>!String(row[key]??'').trim())
const hasInvalidIdentity = (row:Row) => [...internalLineFields].some(key=>{
  const value = String(row[key]??'').trim()
  return !/^\d{1,8}$/.test(value) || Number(value) === 0
})
const extraLineFields = new Set(['accepted','country','product','fob','kg','quantity','unit'])
const emptyChecks: Record<string,Problem[]> = {}
const emptyRows: Row[] = []
const numeric = new Set(['usd','fob','kg','quantity'])

export function LedgerGrid({org, kind, fields}: {org: number; kind: Kind; fields: Fields}) {
  const displayFields = useMemo(() => kind==='lines'
    ? Object.fromEntries(Object.entries(fields).filter(([key])=>key!=='version')
      .map(([key,label])=>[key,key==='line'?'품목란 번호':label]))
    : fields, [fields, kind])
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
  const [hidden, setHidden] = useState<string[]>(() => kind==='lines' ? Object.keys(displayFields).filter(key=>!defaultLineFields.includes(key)) : [])
  const [showExtras, setShowExtras] = useState(false)
  const [showIdentity, setShowIdentity] = useState(false)
  const [issueRows, setIssueRows] = useState<ImportIssue[]>([])
  const validation = useRef(0)
  const grid = useRef<GridApi<Row> | null>(null)
  const nextIssue = useRef(0)
  const form = useRef<HTMLFormElement>(null)
  const excelFile = useRef<HTMLInputElement>(null)
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
    ...(kind==='lines' ? [...defaultLineFields,...Object.keys(displayFields).filter(key=>!defaultLineFields.includes(key))]
      : Object.keys(displayFields)).map(key => ({
      field:key, headerName:displayFields[key], minWidth:key === 'name' ? 195 : 90,
      width:key==='number'||key==='product'?165:key==='line'?105:130,
      valueFormatter:(p:{value:unknown})=>numeric.has(key)
        ? String(p.value??'').replace(/^-?\d+/, value=>value.replace(/\B(?=(\d{3})+(?!\d))/g,',')) : String(p.value??''),
      hide:hidden.includes(key), pinned:key==='number'||key==='line' ? 'left' as const : undefined,
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
        if (isExcludedLine(p.data)) return '제외'
        if (checks.isPending || checks.isError) return '—'
        const problems = rowChecks[String(p.data?.id)] ?? []
        return problems.some(item=>item.severity==='ERROR')?'확정 차단':problems.some(item=>item.severity==='WARNING')?'확인 필요':'통과'
      },
      cellClassRules:{'cell-error':(p: {value:unknown})=>p.value==='확정 차단','cell-warning':(p: {value:unknown})=>p.value==='확인 필요'},
    }] : []),
  ], [displayFields, hidden, kind, rowChecks, checks.isPending, checks.isError])
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
    setShowIdentity(kind==='lines' && (hasInvalidIdentity(initial)
      || (row?.id !== undefined && String(row.id)===params.get('row'))))
    setIssues(rowChecks[String(row?.id)] ?? [])
    void validate(initial)
  }
  const openRequestedRow = useEffectEvent((id:number) => {
    const row = rows.find(row=>row.id===id)
    if (row) select(row)
  })
  const existingRowButton = (problem:Problem) => {
    const row = rows.find(row=>row.id===problem.row_id)
    return row && <button type="button" disabled={busy} onClick={()=>select(row)}>기존 행 {row.id} 열기</button>
  }
  const requestedId = params.has('row') && !query.isFetching
    ? rows.find(row=>String(row.id)===params.get('row'))?.id : undefined
  const openedRow = useRef<number>(undefined)
  useEffect(()=>{
    if (requestedId === undefined || openedRow.current === requestedId) return
    openedRow.current = requestedId
    openRequestedRow(requestedId)
  }, [requestedId])
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
    if (internalLineFields.has(field) && !showIdentity) {
      setShowIdentity(true)
      requestAnimationFrame(() => form.current?.querySelector<HTMLElement>(`[name="${field}"]`)?.focus())
    } else if (extraLineFields.has(field) && !showExtras) {
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
    if (kind==='lines' && !draft.id && !isExcludedLine(draft) && hasInvalidIdentity(draft)) {
      setShowIdentity(true)
      setMessage('품목란 번호와 정정 버전을 확인한 후 저장하세요.')
      await validate(draft)
      return
    }
    const [result] = await send<Result[]>(path, [{id:draft.id, revision:draft.revision, values:values(draft)}])
    ++validation.current
    setIssues(result.issues)
    if (!result.row) {
      const duplicate = result.issues.find(issue=>issue.code==='D080')
      if (result.issues.some(issue=>issue.code==='D012' || issue.code==='D083')) {
        setShowIdentity(true)
        setMessage('기존 품목란과 충돌합니다. 아래의 기존 값과 입력 값을 확인하세요.')
      } else {
        setMessage(duplicate?.message ?? '오류 항목을 수정한 후 저장하세요.')
      }
      return
    }
    const saved = {...result.row, pdf:draft.pdf}
    setDraft(saved)
    setSelected(saved)
    const needsReview = kind==='lines' && result.issues.some(issue=>issue.severity!=='INFO')
    setMessage(needsReview ? '저장됨 · 보완이 필요한 항목이 남아 있습니다.' : '저장됨 · ' + new Date().toLocaleTimeString('ko-KR'))
    await reload()
  }
  const upload = async (file: File) => {
    setIssueRows([])
    const params = new URLSearchParams({name:file.name})
    const response = await request(`org/${org}/import/${kind}?${params}`, {method:'POST', body:file})
    const result: ImportResult = await response.json()
    const duplicates = result.duplicates ?? []
    setIssueRows([...result.rejected, ...duplicates].sort((a,b)=>a.row-b.row))
    if (result.imported) setSearch('')
    const skipped = result.skipped ? ` · 이미 등록된 사업자번호 ${result.skipped}행 건너뜀` : ''
    setMessage(kind==='lines'
      ? `${result.profile} 양식 · ${result.imported}행 원장 등록 · 등록된 행 중 ${result.needs_review}행 보완 필요 · 중복 ${duplicates.length}행 건너뜀 · ${result.rejected.length}행 등록 보류`
      : `${result.profile} 양식 · ${result.imported}행 저장 · ${result.rejected.length}행 보완 필요${skipped}`)
    await reload()
  }
  return <section className="ledger-workspace">
    <div className="toolbar ledger-tools">
      <label>자료 검색<input type="search" placeholder={kind==='lines'?'신고번호 또는 업체명':'업체명 또는 사업자번호'} value={search} onChange={e => setSearch(e.target.value)}/></label>
      {kind==='lines' && <button aria-pressed={problemOnly} onClick={()=>setParams(problemOnly?{}:{filter:'issues'})}>보완 필요만</button>}
      <div className="toolbar-spacer"/>
      {kind==='lines' && <OcrInput org={org} fields={fields} members={members.data} disabled={busy} onSaved={async count=>{
        setMessage(`OCR 승인 · 원장 ${count}행 등록`)
        await reload()
      }}/>}
      <details className="excel-menu"><summary className="button">Excel 가져오기·내보내기</summary>
        <div className="menu-items" onClick={event=>event.currentTarget.parentElement?.removeAttribute('open')}>
        <button disabled={busy} onClick={()=>excelFile.current?.click()}>Excel에서 가져오기</button>
        <button disabled={busy} onClick={() => action(async () => { await download(`org/${org}/export/${kind}`); setMessage('다운로드 완료') })}>Excel 다운로드</button>
        <button disabled={busy} onClick={() => action(async () => { await download(`org/${org}/export/${kind}?template=true`); setMessage('표준 양식 다운로드 완료') })}>표준 양식</button>
        </div>
      </details>
      <input ref={excelFile} hidden type="file" accept=".xlsx" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; e.target.value=''; if (file) void action(() => upload(file))
      }}/>
      <button className={selected?undefined:"primary"} disabled={busy} onClick={() => select()}>{kind==='lines'?'직접 입력':'회원사 추가'}</button>
    </div>
    {params.get('return')?.startsWith('/submission?year=') &&
      <NavLink className="button" to={params.get('return')!}>제출 검토로 돌아가기</NavLink>}
    <details className="compact"><summary>열 표시</summary>
      <div className="checklist">{Object.entries(displayFields).map(([key,label]) => <label key={key}>
        <input type="checkbox" checked={!hidden.includes(key)} onChange={e => setHidden(e.target.checked ? hidden.filter(k => k!==key) : [...hidden,key])}/>
        {label}</label>)}</div>
    </details>
    <div className="ledger-status"><span>{visibleRows.length.toLocaleString('ko-KR')}행</span>
      <span role="status">{busy ? '처리 중…' : message || (dirty ? '저장 전 변경사항' : query.isError || checks.isError ? '자료를 불러오지 못했습니다.' : '행을 선택하여 수정할 수 있습니다.')}</span>
    </div>
    {!!issueRows.length && <details className="compact" open><summary>일괄 입력 검증 · {issueRows.length}행</summary>
      {issueRows.slice(0,200).map(row => <div key={row.row}>
        {row.issues.map(problem => <p key={`${problem.code}-${problem.field}-${problem.row_id}`}>
          {row.row}행 · {problem.message} {existingRowButton(problem)}
        </p>)}
      </div>)}
    </details>}
    <div className="ledger-split"><div className="grid-shell ledger-grid"><AgGridReact<Row> theme={kind==='members' ? memberTheme : theme} onGridReady={event => {grid.current=event.api}}
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
      {draft.pdf && <PdfView key={draft.id} org={org} pdf={draft.pdf}/>}
      {issues.some(p=>p.severity!=='INFO') && <div className="issue-list" aria-label="선택 행 검증 결과">
        <div className="section-top"><b>확인 필요 {new Set(issues.filter(p=>p.severity!=='INFO').map(p=>p.field)).size}개</b><button type="button" onClick={() => {
          const items = issues.filter(p=>p.severity!=='INFO')
          focus(items[nextIssue.current % items.length].field)
          nextIssue.current += 1
        }}>다음 오류로 이동</button></div>
        {kind==='lines' && <ul>
          {issues.filter(problem=>problem.severity!=='INFO').map(problem=>
            <li key={`${problem.field}-${problem.code}-${problem.row_id}`}>
              <button type="button" onClick={()=>focus(problem.field)}>
                {fields[problem.field]} · {problem.message}
              </button>
              {existingRowButton(problem)}
            </li>)}
        </ul>}
      </div>}
      {kind==='lines' && <p className="muted">실적 연도 정책이 지정한 날짜를 입력하세요. 순중량 등 참고 항목은 선택입니다.</p>}
      {kind==='lines' && <button className="extra-toggle" type="button" aria-expanded={showIdentity} onClick={()=>setShowIdentity(!showIdentity)}>
        {showIdentity?'품목란 번호·정정 버전 접기':'여러 품목란 또는 정정 신고인 경우'}
      </button>}
      <fieldset disabled={busy} className="entry">
        {(() => {
          const renderField = ([key,label]: [string,string]) => {
          const problems = issues.filter(p => p.field === key && p.severity!=='INFO')
          const role=key.split('_')[0]
          const stateKey=role+'_state'
          const roleField=kind==='lines'&&roleKeys.has(role)
          if (roleField&&key==='maker_state') return null
          const number=normalizeNumber(draft[role])
          const roleName=String(draft[role+'_name']??'').trim()
          const member=roleField&&members.data?.find(row=>normalizeNumber(row.number)===number)
          const savedState=String(draft[stateKey]??'')
          const state=savedState==='비회원 확인'?'회원사 아님':savedState
          const matches=member&&!!normalizeCompany(roleName)&&
            memberNames(member).includes(normalizeCompany(roleName))
          const stateOptions=!number&&!roleName?['원본에 없음','회원사 아님']:
            member?[]:['회원사 아님']
          const choices=key===stateKey&&roleField
            ? [...new Set([...stateOptions,...(state&&state!=='회원 대조'?[state]:[])])]
            : options[key]
          const change = (value: string) => {
            const next = {...draft,[key]:value}
            if (roleField && key!==stateKey) {
              const nextNumber=normalizeNumber(next[role])
              const registered=members.data?.some(row=>normalizeNumber(row.number)===nextNumber)
              next[stateKey]=state==='회원사 아님'&&!registered?state:''
            }
            setDraft(next)
            void validate(next)
          }
          const memberName = /^(agent|owner|maker)_name$/.test(key)
          const queryName = memberName ? normalizeCompany(roleName) : ''
          const candidates = queryName && !matches ? (members.data??[])
            .map(row=>({row,rank:Math.min(...memberNames(row).map(name=>nameRank(queryName,name)))}))
            .filter(({row,rank})=>/^\d{10}$/.test(normalizeNumber(row.number))&&Number.isFinite(rank)&&
              (role!=='maker'||row.kind==='생산자'||row.kind==='복수'))
            .sort((a,b)=>a.rank-b.rank)
            .slice(0,5) : []
          const inputId=`ledger-${kind}-${key}`
          const value=roleField&&key===stateKey?state:String(draft[key]??'')
          if (roleField&&key===stateKey&&!choices?.length) {
            const hasIssue=issues.some(p=>p.severity!=='INFO'&&[role,role+'_name',stateKey].includes(p.field))
            return matches&&!hasIssue
              ? <p className="muted" key={key}>{fields[role+'_name']} · 회원사 명부 자동 대조 완료</p>
              : null
          }
          return <div className="field-entry" key={key}><div className="field-caption"><label htmlFor={inputId}>{label}{kind==='lines'&&extraLineFields.has(key)?' (선택)':''}</label>
            <HelpTip text={ledgerHelp(key,kind)??label}/></div>
            <div className="error-anchor">
            {choices ? <select id={inputId} name={key} aria-describedby={problems.length?`${inputId}-error`:undefined} aria-invalid={!!problems.length} value={choices.includes(value)?value:''} onChange={e => change(e.target.value)}>
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
          const entries = Object.entries(displayFields)
          if (kind!=='lines') return entries.map(renderField)
          return <>
            {showIdentity && <fieldset className="party-fields">
              <legend>품목란 번호와 정정 버전</legend>
              <p className="muted">품목란이 하나인 최초 신고는 둘 다 1입니다. 품목란이 여러 개면 원본의 번호를, 정정 신고면 확인한 정정 차수를 입력하세요.</p>
              {hasMissingIdentity(draft) && <button type="button" onClick={()=>{
                const next = {...draft}
                for (const key of internalLineFields) {
                  if (!String(next[key]??'').trim()) next[key]='1'
                }
                setDraft(next)
                void validate(next)
              }}>비어 있는 항목에 1 넣기</button>}
              {[...internalLineFields].map(key=>renderField([key,fields[key]]))}
            </fieldset>}
            {entries.filter(([key])=>!internalLineFields.has(key)&&!roleKeys.has(key.split('_')[0])&&!extraLineFields.has(key)).map(renderField)}
            {[...roleKeys].map(role=><fieldset className="party-fields" key={role}>
              <legend>{fields[role+'_name']}</legend>
              {role==='maker' && <ProducerSelect members={members.data} onSelect={member=>{
                const next={...draft,maker:String(member.number),maker_name:String(member.name),maker_state:''}
                setDraft(next)
                void validate(next)
              }}/>}
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
        {kind==='members' && selected.id && <button type="button" className="danger" disabled={busy} onClick={() => {
          if (!window.confirm(`‘${selected.name}’ 회원사를 명부에서 삭제하시겠습니까?`)) return
          void action(async () => {
            await request(`${path}/${selected.id}?revision=${selected.revision}`, {method:'DELETE'})
            ++validation.current
            setSelected(undefined)
            setDraft({})
            setIssues([])
            await reload()
            setMessage('회원사 삭제 완료')
          })
        }}>회원사 삭제</button>}
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
