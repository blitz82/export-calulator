import { lazy, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { HashRouter, Navigate, NavLink, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { download, get, send } from './api'
import type { Fields, Problem, Row } from './grid'
import { HelpTip } from './help-tip'
import { PdfView } from './pdf-view'
import './style.css'
import {WorkflowProvider,WorkflowBar,WorkflowRoute,WorkbenchHome,WorkspaceIntro,ArchiveWorkspace} from './workflow'

const LedgerGrid = lazy(() => import('./grid').then(module => ({default:module.LedgerGrid})))
const FacilityRegistry = lazy(() => import('./facility-registry').then(module => ({default:module.FacilityRegistry})))
const ContractsWorkspace = lazy(() => import('./contracts').then(module => ({default:module.ContractsWorkspace})))
const SortingWorkspace = lazy(() => import('./settlement').then(module => ({default:module.SettlementWorkspace})))
const SortingIntakeWorkspace = lazy(() => import('./sorting-intake').then(module => ({default:module.SortingIntakeWorkspace})))
const SupportBudgetWorkspace = lazy(() => import('./support-budget').then(module => ({default:module.SupportBudgetWorkspace})))
const MonthlyReportWorkspace = lazy(() => import('./monthly-report').then(module => ({default:module.MonthlyReportWorkspace})))
const menus = [
  ['/', '업무 안내', '자료 등록·월별 정산·연간 실적의 이동 경로를 안내합니다.'],
  ['/members', '회원사 명부', '회원사를 추가하거나 Excel 명부를 가져옵니다.'],
  ['/lines', '수출신고필증 등록', '수출신고필증의 품목란을 입력하고 보완합니다.'],
  ['/calculate', '수출실적 계산', '연도별 정책을 적용해 직접·간접 수출실적을 계산합니다.'],
  ['/results', '회원사별 실적 확인', '회원사별 인정 금액과 신고 품목란별 근거를 확인합니다.'],
  ['/submission', '회원사별 공동정산 수출실적', '선택 연도와 직전 연도의 제출용 Excel을 준비합니다.'],
  ['/audit', '감사 자료', '계산 근거, 원장 변경 이력 및 감사 증빙을 조회합니다.'],
  ['/checks', '상세 검증 이력', '이전 계산의 입력 및 계산 문제를 확인합니다.'],
  ['/settings', '프로그램 설정', '품목과 연도별 계산 정책을 등록하고 데이터 백업을 내려받습니다.'],
  ['/help', '도움말', '공동정산 처리 순서와 실적 인정 기준을 확인합니다.'],
] as const
type Org = {id:number; name:string}
type Run = {id:number; year:number; time:string}
type Detail = {accepted_lines:number; actual_usd:string; attributed_usd:string; actual_kg:string|null; ready:boolean; rule:string;
  summary:Row[]; ledger:Row[]; checks:Problem[]; member_issues:Problem[]}
const numericKey = (key:string) => /(?:^|_)(usd|kg)$/.test(key) || key==='lines'
const formatNumber = (value:unknown) => String(value??'').replace(/^-?\d+/, n=>n.replace(/\B(?=(\d{3})+(?!\d))/g,','))
const summaryFields = {name:'회원사명',number:'사업자번호',direct_usd:'직접 USD',indirect_usd:'간접 USD',
  total_usd:'합계 USD',direct_kg:'직접 kg',indirect_kg:'간접 kg',lines:'인정 품목란'}
const ledgerFields = {number_declared:'신고번호',line:'품목란',name:'회원사',kind:'인정유형',usd:'금액 (달러)',kg:'중량 (킬로그램)',reason:'판정 근거'}
const auditLabels: Fields = {id:'기록 번호',revision:'수정 차수',source:'원본 행 번호',year:'실적 연도',
  cutoff:'회원자격 기준일',basis:'귀속 기준',hs:'대상 HS코드',rule:'계산 규칙 버전',sha:'파일 해시',
  rows:'행 수',active_count:'활성 행 수',run:'계산 실행번호',previous_run:'직전 연도 계산 실행번호',
  first:'직전 연도 계산 실행번호',second:'선택 연도 계산 실행번호',name:'이름'}
const entityLabels: Fields = {lines:'신고 원장',members:'회원사 명부',Excel:'엑셀 파일'}
const severityLabels: Fields = {ERROR:'오류',WARNING:'경고',INFO:'알림'}
const basisLabels: Fields = {shipped:'선적일',declared:'신고일'}

function auditText(value:unknown, fields:Fields): string {
  if (!value || typeof value!=='object' || Array.isArray(value)) return String(value??'')
  return Object.entries(value).map(([key,item])=>{
    const shown=key==='basis'?basisLabels[String(item)]??item:item
    return `${fields[key]??auditLabels[key]??'기타 항목'} ${auditText(shown,fields)||'—'}`
  }).join(' · ')
}

function useAction() {
  const [status,setStatus] = useState('')
  const [busy,setBusy] = useState(false)
  const run = async (task: () => Promise<string>) => {
    setBusy(true); setStatus('')
    try { setStatus(await task()) } catch (error) { setStatus((error as Error).message) }
    finally { setBusy(false) }
  }
  return {run,busy,status:busy?'처리 중…':status}
}

function Table({rows,fields,onPick,editLink,pdfOrg,searchLabel='표 검색', editLabel='신고 행 열기'}: {
  rows: Row[]; fields: Fields; onPick?: (row:Row) => void; editLink?: (row:Row) => string | undefined;
  pdfOrg?:number; searchLabel?:string; editLabel?:string
}) {
  const [search,setSearch] = useState('')
  const [sort,setSort] = useState<{key:string;reverse:boolean}>()
  const [page,setPage] = useState(0)
  const items = rows.filter(r => Object.keys(fields).some(k => String(r[k] ?? '').toLowerCase().includes(search.toLowerCase())))
  if (sort) items.sort((a,b) => (sort.reverse?-1:1)*(numericKey(sort.key) ?
    Number(a[sort.key]??0)-Number(b[sort.key]??0) : String(a[sort.key]??'').localeCompare(String(b[sort.key]??''),'ko',{numeric:true})))
  const last = Math.max(0, Math.ceil(items.length/100)-1)
  const active = Math.min(page,last)
  const showPdf = pdfOrg !== undefined && rows.some(row=>row.pdf)
  return <section className="panel">
    <div className="toolbar"><label>{searchLabel}<input type="search" value={search}
      onChange={e => {setSearch(e.target.value); setPage(0)}}/></label><span className="muted">{items.length.toLocaleString('ko-KR')}건</span></div>
    <div className="table-scroll"><table><thead><tr>{Object.entries(fields).map(([key,label]) => <th key={key} className={numericKey(key)?'numeric':undefined}>
      <button onClick={() => setSort({key,reverse:sort?.key===key && !sort.reverse})}>{label}{sort?.key===key ? sort.reverse?' (내림차순)':' (오름차순)':''}</button></th>)}
      {onPick && <th>상세</th>}{editLink && <th>수정</th>}{showPdf && <th>원본 PDF</th>}</tr></thead><tbody>{items.slice(active*100,active*100+100).map((row,i) => <tr key={i}>
      {Object.keys(fields).map(key => <td key={key} className={numericKey(key)?'numeric':undefined}>{numericKey(key)?formatNumber(row[key]):String(row[key]??'')}</td>)}
      {onPick && <td><button onClick={() => onPick(row)}>보기</button></td>}
      {editLink && <td>{editLink(row) && <NavLink to={editLink(row)!}>{editLabel}</NavLink>}</td>}
      {showPdf && <td>{row.pdf && <PdfView key={row.pdf.file+':'+row.pdf.page} org={pdfOrg} pdf={row.pdf}/>}</td>}</tr>)}</tbody></table></div>
    {!items.length && <p className="muted">조회된 자료가 없습니다.</p>}
    {last > 0 && <div className="toolbar"><button disabled={!active} onClick={() => setPage(active-1)}>이전</button>
      <span>{active+1} / {last+1}</span><button disabled={active===last} onClick={() => setPage(active+1)}>다음</button></div>}
  </section>
}

function Dashboard({org}: {org:Org}) {
  const prefix = `org/${org.id}`
  const members = useQuery({queryKey:[prefix+'/data/members'],queryFn:()=>get<Row[]>(prefix+'/data/members')})
  const lines = useQuery({queryKey:[prefix+'/data/lines'],queryFn:()=>get<Row[]>(prefix+'/data/lines')})
  const checksPath=prefix+'/line-checks'
  const checks=useQuery({queryKey:[checksPath],queryFn:()=>get<Record<string,Problem[]>>(checksPath)})
  const count=checks.data && Object.values(checks.data).filter(issues=>issues.some(p=>p.severity!=='INFO')).length
  const next = !members.data || !lines.data || count===undefined ? undefined
    : !members.data.length ? ['/members','회원사 명부를 등록하세요.','회원사 등록하기']
    : !lines.data.length ? ['/lines','수출신고필증을 입력하세요.','신고필증 입력하기']
    : ['/submission','실적 연도를 선택하고 제출자료를 검토하세요.','제출자료 열기']
  return <>
    <section className="panel next-action"><h2>지금 할 일</h2>
      {next ? <><p>{next[1]}</p><NavLink className="button primary" to={next[0]}>{next[2]}</NavLink></>
        : <p role="status">{[members,lines,checks].some(query=>query.isError)
          ? '업무 현황을 불러오지 못했습니다.' : '업무 현황을 불러오는 중…'}</p>}
    </section>
    <div className="metrics">
      <NavLink className="metric metric-link" to="/lines?filter=issues"><span>보완 필요 원장 행</span>
        <strong>{count?.toLocaleString('ko-KR')??'—'}</strong> <small>행 · 원장 열기</small></NavLink>
      <NavLink className="metric metric-link" to="/submission"><span>수출기업육성부 제출자료</span>
        <strong>제출 준비</strong><small className="metric-detail">연도별 준비 상태 확인</small></NavLink>
    </div>
    <section className="panel dashboard-inventory"><h2>등록 현황</h2>
      <NavLink to="/members">회원사 <b>{members.data?.length.toLocaleString('ko-KR')??'—'}개사</b></NavLink>
      <NavLink to="/lines">신고 원장 <b>{lines.data?.length.toLocaleString('ko-KR')??'—'}행</b></NavLink></section>
  </>
}

function Settings({org,choices,refresh}: {org?:Org;choices:Org[];refresh: (item:Org)=>void}) {
  const [onboarding] = useState(!org)
  const {hash} = useLocation()
  useEffect(()=>{
    if (hash==='#calculation-policy') document.getElementById('calculation-policy')?.focus()
  },[hash,org])
  const [name,setName] = useState(org?.name??'')
  const [choice,setChoice] = useState('')
  const [year,setYear] = useState('2026')
  const [cutoff,setCutoff] = useState('')
  const [basis,setBasis] = useState('')
  const [hs,setHs] = useState('')
  const [reason,setReason] = useState('사업지침')
  const [resetText,setResetText] = useState('')
  const [backedUp,setBackedUp] = useState(false)
  const action=useAction()
  const client=useQueryClient()
  const prefix = `org/${org?.id}`
  const policies = useQuery({queryKey:[prefix+'/policies'],queryFn:()=>get<{year:number; cutoff:string; basis:string; hs:string[]}[]>(prefix+'/policies'),enabled:!!org})
  const priorPolicy=policies.data?.find(policy=>policy.year===Number(year)-1)
  return <>
    {choices.length>0 ? <form className="panel" onSubmit={e=>{e.preventDefault();void action.run(async()=>{
      refresh(await send<Org>('office/'+choice,undefined,'PUT'))
      return '사용 품목을 지정했습니다.'
    })}}><h2>사용 품목 지정</h2><p>기존에 등록한 품목 중 이 사무국에서 사용할 품목 하나를 지정하세요.</p>
      <div className="toolbar"><label>사용 품목<select required value={choice} onChange={e=>setChoice(e.target.value)}>
        <option value="">품목 선택</option>{choices.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label><button className="primary" disabled={action.busy||!choice}>지정</button></div></form> :
    <form className="panel" onSubmit={e => {e.preventDefault(); void action.run(async () => {
      const result=await send<Org>((org?'office/'+org.id:'organizations')+'?name='+encodeURIComponent(name),undefined,org?'PUT':'POST'); refresh(result); setName(result.name); return org?'품목명을 수정했습니다.':'품목을 등록했습니다.'
    })}}><h2>{org?'품목 정보':'품목 등록'}</h2>
      {!org&&<p>관리할 농산물 이름을 입력하세요. 등록 후 회원사 명부를 입력할 수 있습니다.</p>}
      <div className="toolbar"><label>품목명<input required placeholder="예: 감귤" value={name}
      onChange={e=>setName(e.target.value)}/></label><button className={org?undefined:'primary'} disabled={action.busy}>{org?'품목명 저장':'품목 등록'}</button></div></form>}
    {onboarding&&org&&<section className="panel next-action"><h2>품목 등록 완료</h2><NavLink className="button primary" to="/members">회원사 등록하기</NavLink></section>}
    {org && <form className="panel" id="calculation-policy" tabIndex={-1} onSubmit={e => {e.preventDefault(); void action.run(async () => {
      await send(prefix+'/policies/'+year,{cutoff,basis,hs:hs.split(/[,，\s]+/).filter(Boolean),reason},'PUT')
      await policies.refetch(); return '계산 정책을 저장했습니다.'
    })}}><h2>연도별 계산 정책</h2>
      {priorPolicy && <button type="button" onClick={()=>{
        setCutoff('')
        setBasis(priorPolicy.basis)
        setHs(priorPolicy.hs.join('\n'))
        setReason('')
      }}>전년도 귀속 기준·HS코드 불러오기</button>}
      <div className="entry">
        <label><span>실적 연도 <HelpTip text="수출실적을 조회하거나 계산할 연도입니다."/></span><input required type="number" min="1900" max="2100" value={year} onChange={e=>setYear(e.target.value)}/></label>
        <label><span>회원자격 기준일 <HelpTip text="가입과 탈퇴 날짜를 이 날짜와 비교해 회원 자격을 판단합니다."/></span><input required type="date" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>
        <label><span>귀속연도 기준 <HelpTip text="신고일과 선적일 중 실적 연도를 결정할 날짜를 선택합니다."/></span><select required value={basis} onChange={e=>setBasis(e.target.value)}>
          <option value="">선택</option><option value="shipped">선적일</option><option value="declared">신고일</option></select></label>
        <label><span>대상 HS코드 <HelpTip text="입력한 코드 중 하나와 일치하면 해당 연도의 대상 품목으로 검증합니다."/></span><textarea required aria-label="대상 HS코드" placeholder={'0805211000\n0805220000'} value={hs} onChange={e=>setHs(e.target.value)}/><small>쉼표 또는 줄바꿈으로 여러 코드를 입력하세요.</small></label>
        <label><span>확정 근거 <HelpTip text="이 정책을 정한 공문, 제출 안내 또는 내부 승인 기록을 입력합니다."/></span><input required value={reason} onChange={e=>setReason(e.target.value)}/></label>
      </div><p className="muted">연도별 제출자료에 이 정책의 회원자격 기준일을 표시합니다.</p>
      <button className="primary" disabled={action.busy}>정책 저장</button>
    </form>}
    {org && <Table searchLabel="연도별 기준검색" rows={(policies.data??[]).map(row=>({...row,
      basis:row.basis==='shipped'?'선적일':row.basis==='declared'?'신고일':row.basis,
      hs:Array.isArray(row.hs)?row.hs.join(', '):row.hs}))} fields={{year:'연도',cutoff:'기준일',basis:'귀속 기준',hs:'대상 HS코드'}}/>}
    <section className="panel"><h2>데이터 백업</h2><button disabled={action.busy} onClick={() => action.run(async () => {
      await download('backup'); setBackedUp(true); return '백업 다운로드 완료'
    })}>백업 파일 다운로드</button></section>
    {org && <details className="compact"><summary>신고 원장 전체 삭제</summary>
      <form className="panel" onSubmit={e=>{
        e.preventDefault()
        if (!backedUp || resetText!=='모두리셋') return
        void action.run(async()=>{
          const result = await send<{count:number}>(prefix+'/data/lines/reset',{confirmation:resetText})
          setResetText('')
          setBackedUp(false)
          await Promise.all(['data/lines','line-checks'].map(key=>client.invalidateQueries({queryKey:[prefix+'/'+key]})))
          return `원장 ${result.count.toLocaleString('ko-KR')}행을 비웠습니다.`
        })
      }}>
        <p><b>{org.name}</b>의 활성 신고 원장 전체를 비웁니다. 원본 파일, 계산 이력 및 확정본은 보존됩니다.</p>
        <p>백업 파일을 내려받아 보관한 뒤 <strong>모두리셋</strong>을 입력하세요.</p>
        <div className="toolbar">
          <label>리셋 확인 문구<input autoComplete="off" value={resetText} onChange={e=>setResetText(e.target.value)}/></label>
          <button className="danger" disabled={action.busy||!backedUp||resetText!=='모두리셋'}>모든 행 비우기</button>
        </div>
      </form>
    </details>}
    <p className="status" role="status">{action.status}</p>
  </>
}

type Review = {
  missing:number[]; saved:boolean
  results:(Pick<Detail,'ready'|'summary'|'actual_usd'|'attributed_usd'|'actual_kg'> & {
    year:number; id:number; cutoff:string; checks:(Problem & {member_id?:number; subject?:string})[]
  })[]
  history:{first_run:number; second_run:number; time:string}[]
}

function SubmissionWorkspace({org,fields}: {org:Org;fields?:Record<'members'|'lines',Fields>}) {
  const prefix = `org/${org.id}`
  const [params,setParams] = useSearchParams()
  const action = useAction()
  const client = useQueryClient()
  const policies = useQuery({queryKey:[prefix+'/policies'],queryFn:()=>get<{year:number}[]>(prefix+'/policies')})
  const runs = useQuery({queryKey:[prefix+'/runs'],queryFn:()=>get<Run[]>(prefix+'/runs')})
  const years = [...new Set([...(policies.data??[]),...(runs.data??[])].map(item=>item.year))].sort((a,b)=>b-a)
  const requested = Number(params.get('year'))
  const year = years.includes(requested) ? requested : years[0]
  const path = prefix+'/submission-review?year='+year
  const review = useQuery({queryKey:[path],queryFn:()=>get<Review>(path),enabled:year!==undefined,refetchOnMount:'always'})
  const data = review.data
  const [prior,current] = data?.results ?? []
  const complete = !!prior && !!current
  const annualPath = (first:number,second:number) => `${prefix}/annual-submission?previous_id=${first}&run_id=${second}`
  const currentPath = complete ? annualPath(prior.id,current.id) : ''
  const returnTo = encodeURIComponent('/submission?year='+year)
  const error = policies.error ?? runs.error ?? review.error
  return <>
    <section className="panel">
      <div className="toolbar">
        <label>실적 연도<select aria-label="실적 연도" value={year??''} disabled={action.busy}
          onChange={e=>setParams({year:e.target.value})}>
          {!years.length&&<option value="">정책 등록 필요</option>}
          {years.map(value=><option key={value} value={value}>{value}년</option>)}
        </select></label>
        {data && !data.missing.length && !complete && <button className="primary" disabled={action.busy||review.isFetching}
          onClick={()=>action.run(async()=>{
            client.setQueryData([path],await send<Review>(path))
            await runs.refetch()
            return '현재 자료 검토를 완료했습니다.'
          })}>현재 자료 검토</button>}
      </div>
      {year!==undefined&&<p>제출 대상 실적 <b>{year-1}년 · {year}년</b></p>}
      {!!data?.missing.length && <p>
        <NavLink to="/settings#calculation-policy">{data.missing.join('년, ')}년 계산 정책 등록</NavLink></p>}
      {year===undefined&&policies.isSuccess&&runs.isSuccess&&<NavLink to="/settings#calculation-policy">연도별 정책 등록</NavLink>}
      {!!data?.history.length && !data.saved && <p className="pill pill-warning">확정 이후 자료 변경 또는 재검토 · 이전 확정본 보관됨</p>}
      {data && !complete && !data.missing.length && <p>현재 자료로 두 연도의 실적을 검토하세요.</p>}
      {complete && <div className="toolbar">
        <button disabled={action.busy||review.isFetching} onClick={()=>action.run(async()=>{
          await download(currentPath,'POST')
          return '초안 다운로드 완료'
        })}>초안 다운로드</button>
        <button className="primary" disabled={action.busy||review.isFetching||review.isError||!prior.ready||!current.ready} onClick={()=>{
          if (!data?.saved&&!window.confirm('검토한 두 연도의 제출자료를 확정하시겠습니까?')) return
          void action.run(async()=>{
            try { await download(currentPath+'&final=true&require_current=true','POST') }
            finally { await review.refetch() }
            return '확정본 다운로드 완료'
          })
        }}>{data?.saved?'현재 확정본 다운로드':'최종 확정 후 제출용 Excel 다운로드'}</button>
      </div>}
      <p role="status">{action.status || error?.message || (review.isFetching?'현재 자료를 확인하는 중…':'')}</p>
    </section>
    {data?.results.slice().reverse().map(result=><section key={result.year} className="panel">
      <h2>{result.year}년 실적 검토</h2>
      <p>회원자격 기준일 {result.cutoff} · {result.ready?'검증 통과':`보완 필요 ${result.checks.length}건`}</p>
      {!result.ready && <Table rows={result.checks.map(problem=>({...problem,
        field:(problem.member_id?fields?.members:fields?.lines)?.[problem.field]??'기타 항목',
      }))} fields={{subject:'회원사명 또는 신고번호',field:'항목',message:'보완 내용'}} editLabel="수정하기"
        editLink={row=>row.member_id ? `/members?row=${row.member_id}&return=${returnTo}` :
          row.row_id ? `/lines?row=${row.row_id}&return=${returnTo}` : undefined}/>}
      {result.checks.some(problem=>problem.code==='D090'||problem.code==='D092')&&<p role="alert">계약 관계 확인이 필요하여 최종 확정할 수 없습니다. <NavLink to="/contracts">계약서 등록 확인</NavLink></p>}
      <p>신고 기준 실수출액 <b>{formatNumber(result.actual_usd)} USD</b> · 회원사 귀속 인정액 <b>{formatNumber(result.attributed_usd)} USD</b>
        {result.actual_kg!==null&&result.actual_kg!=='0'&&<> · 신고 기준 순중량 <b>{formatNumber(result.actual_kg)} kg</b></>}</p>
      <details className="compact" open={result.ready}><summary>회원사별 실적</summary>
        <Table rows={result.summary} fields={{name:'회원사명',direct_usd:'직접 USD',indirect_usd:'간접 USD',total_usd:'합계 USD'}}/>
        <NavLink to={`/results?run=${result.id}`}>신고별 인정 근거 보기</NavLink>
      </details>
    </section>)}
    {!!data?.history.length && <details className="compact"><summary>보관된 확정본</summary>
      {data.history.map(item=><div className="toolbar" key={item.second_run+':'+item.first_run}>
        <span>검토 시각 {item.time.slice(0,16).replace('T',' ')}</span>
        <button disabled={action.busy} onClick={()=>action.run(async()=>{
          await download(annualPath(item.first_run,item.second_run)+'&final=true','POST')
          return '보관된 확정본 다운로드 완료'
        })}>보관본 다운로드</button>
      </div>)}
    </details>}
  </>
}

function RunWorkspace({org,mode,fields}: {org:Org;mode:string;fields?:Record<'members'|'lines',Fields>}) {
  const prefix = `org/${org.id}`
  const [year,setYear]=useState(String(new Date().getFullYear()))
  const [params]=useSearchParams()
  const [runId,setRunId]=useState(params.get('run')??'')
  const [number,setNumber]=useState('')
  const [rowId,setRowId]=useState<number>()
  const action=useAction()
  const runs=useQuery({queryKey:[prefix+'/runs'],queryFn:()=>get<Run[]>(prefix+'/runs')})
  const current=runId || String(runs.data?.[0]?.id ?? '')
  const detail=useQuery({queryKey:[prefix,'run',current],queryFn:()=>get<Detail>(`${prefix}/runs/${current}`),enabled:!!current})
  const history=useQuery({queryKey:[prefix,'history',rowId],queryFn:()=>get<Row[]>(prefix+'/audit'+(rowId?'?row_id='+rowId:'')),
    enabled:mode==='audit'||!!rowId})
  const evidence=useQuery({queryKey:[prefix,'evidence',current,rowId],queryFn:()=>get<{row:Row;source:Row|null;decisions:Row[]}>(`${prefix}/runs/${current}/evidence/${rowId}`),
    enabled:!!rowId && !!current})
  const data=detail.data
  const chooser=(value:string,change:(value:string)=>void, label:string)=>
    <label><span>{label} <HelpTip text="계산할 때마다 생성되는 번호입니다. 선택한 실행 당시의 계산 및 검증 결과를 조회합니다."/></span><select aria-label={label} value={value} onChange={e=>change(e.target.value)}><option value="">선택</option>
      {runs.data?.map(r=><option key={r.id} value={r.id}>{mode==='audit'
        ? `#${r.id} · ${r.time.slice(0,10)}` : `${r.year}년 · #${r.id} · ${r.time}`}</option>)}</select></label>
  return <>
    <section className="panel">
      {mode==='calculate' && <form className="toolbar" onSubmit={e=>{e.preventDefault(); void action.run(async()=>{
          const result=await send<{id:number;ready:boolean}>(prefix+'/runs?year='+encodeURIComponent(year))
          await runs.refetch(); setRunId(String(result.id)); setRowId(undefined)
          return result.ready?'계산 완료 · 검증 통과':'계산 완료 · 검증 항목을 보완하세요.'
        })}}><label><span>실적 연도 <HelpTip text="수출실적을 조회하거나 계산할 연도입니다."/></span><input aria-label="실적 연도" type="number" required min="1900" max="2100" value={year} onChange={e=>setYear(e.target.value)}/></label>
        <button className="primary" disabled={action.busy}>수출실적 계산</button>
        <NavLink to="/settings#calculation-policy">연도별 계산 정책</NavLink>
      </form>}
      {mode==='results'&&<div className="toolbar"><NavLink className="button primary" to="/submission">제출자료</NavLink></div>}
      <details className="compact" open={mode==='audit'||mode==='checks'||undefined}>
        <summary>계산 기록 상세</summary>
        {data&&<p className="muted">계산 규칙 버전 {data.rule}</p>}
        <div className="toolbar">
      {chooser(current,id=>{setRunId(id);setNumber('');setRowId(undefined)},'계산 실행번호')}
      <button disabled={!current||action.busy} onClick={()=>action.run(async()=>{
        await download(`${prefix}/runs/${current}/report`); return 'Excel 다운로드 완료'
      })}>계산 검증 Excel</button>
      {mode==='audit' && <button disabled={!current||action.busy} onClick={()=>action.run(async()=>{
        await download(`${prefix}/runs/${current}/report?bundle=true`); return '감사증빙 다운로드 완료'
      })}>감사증빙 ZIP</button>}
        </div>
      </details>
      <p className="status" role="status">{action.status || (detail.isError ? (detail.error as Error).message : '')}</p></section>
    {data && <>
      <div className="metrics">{[['신고 기준 실수출액',data.actual_usd,'USD'],['회원사 귀속 인정액',data.attributed_usd,'USD'],
        ['신고 기준 순중량',data.actual_kg,'kg'],['검증 통과 품목란',data.accepted_lines,'건']].filter(([,value])=>value!==null).map(([label,value,unit]) =>
        <div className="metric" key={label}><span>{label}</span><strong>{value===null?'미집계':formatNumber(value)}</strong> <small>{value===null?'':unit}</small></div>)}</div>
      <p className={`pill ${data.ready?'pill-success':'pill-warning'}`}>{data.ready?'계산 검증 통과':'계산 보완 필요'}</p>
      {mode==='calculate'&&!data.ready&&<div className="toolbar">
        <NavLink to="/lines?filter=issues">보완 원장 열기</NavLink>
        <NavLink to="/members">회원사 명부 확인</NavLink>
        <NavLink to={`/checks?run=${current}`}>상세 검증 이력</NavLink>
      </div>}
      {mode==='checks' || mode==='calculate' ? <Table rows={[
        ...data.checks.map(row=>({...row,field:fields?.lines[row.field]??'기타 항목',severity:severityLabels[row.severity]??'기타 상태'})),
        ...data.member_issues.map(row=>({...row,field:fields?.members[row.field]??'기타 항목',severity:severityLabels[row.severity]??'기타 상태'})),
      ]}
        fields={{row_id:'원장 번호',field:'항목',severity:'심각도',message:'검증 내용'}}
        editLink={r=>r.row_id && r.severity!=='알림' ? `/lines?row=${r.row_id}` : undefined}
        onPick={r=>setRowId(r.row_id ? Number(r.row_id) : undefined)}/> : <>
        <Table rows={data.summary} fields={summaryFields} onPick={r=>{setNumber(String(r.number));setRowId(undefined)}}/>
        <details className="compact" open={!!number || mode==='audit'} key={number||'all'}><summary>신고 품목란별 인정 내역</summary>
        <div className="section-top"><h2 className="recognition-heading">{number ? String(data.summary.find(row=>row.number===number)?.name ?? number) : '전체 회원사'}</h2>
          {number && <button onClick={()=>{setNumber('');setRowId(undefined)}}>뒤로</button>}</div>
        <Table rows={data.ledger.filter(r=>!number||r.number===number)} fields={ledgerFields} pdfOrg={org.id} onPick={r=>setRowId(Number(r.row_id))}/>
        </details>
      </>}
    </>}
    {evidence.data && <section className="panel"><h2>신고 원장 근거 · #{rowId}</h2>
      <dl>{Object.entries(evidence.data.row).map(([key,value])=><div key={key}><dt>{fields?.lines[key]??auditLabels[key]??'기타 항목'}</dt><dd>{String(value??'')||'—'}</dd></div>)}</dl>
      {evidence.data.source && <p>원본 {String(evidence.data.source.name)} · {String(evidence.data.source.sheet)} · {String(evidence.data.source.row)}행</p>}
      <Table rows={evidence.data.decisions} fields={ledgerFields}/></section>}
    {(mode==='audit'||rowId) && <details className="compact" key={rowId??'audit'}><summary>변경 이력</summary><Table rows={(history.data??[]).map(r=>{
      const entity=String(r.entity)
      const labels=entity==='lines'||entity==='members'?fields?.[entity]??{}:{}
      return {...r,entity:entityLabels[entity]??entity,before:auditText(r.before,labels),after:auditText(r.after,labels)}
    })}
      fields={{time:'변경 시각',actor:'사용자',entity:'대상',target:'기록 번호',reason:'사유',before:'변경 전',after:'변경 후'}}/></details>}
  </>
}

function Help() {
  return <section className="panel guide"><h2>공동정산 업무 순서</h2><ol>
    <li><NavLink to="/settings">프로그램 설정</NavLink>에서 품목을 등록하고 연도별 회원 기준일, 귀속일, 대상 HS코드를 저장합니다.</li>
    <li><NavLink to="/members">회원사 명부</NavLink>에서 명부를 등록합니다. 역할 사업자는 사업자번호와 등록 이름 또는 별칭으로 대조합니다.</li>
    <li><NavLink to="/lines">신고 원장</NavLink>에 신고필증을 등록합니다. 한 신고에 품목란이 여러 개이거나 정정 신고라면 해당 항목을 펼쳐 원본 값을 확인하세요.</li>
    <li><NavLink to="/submission">제출자료</NavLink>에서 실적 연도를 선택하고 현재 자료를 검토합니다. 보완할 건을 수정하고 회원사별 실적을 확인한 뒤 제출용 Excel을 확정합니다.</li>
  </ol><h2>인정 기준</h2><p>대행자와 화주는 직접수출 자격, 제조자는 간접수출 자격을 가집니다. 같은 사업자가 두 자격을 갖는 경우 직접수출을 한 번 인정합니다.</p>
  <p>동일 신고 품목란의 실제 수출액은 한 번 집계합니다. 서로 다른 회원사에는 해당 역할에 따른 실적이 각각 귀속됩니다.</p>
  <p><a href="/oss/index.html" target="_blank" rel="noreferrer">오픈소스 소프트웨어 정보</a></p></section>
}

function MenuLink({item:[path,label,description]}: {item:typeof menus[number]}) {
  return <NavLink to={path} end aria-description={description}>{label}</NavLink>
}

function WorkMenu() {
  const sorting=useQuery({queryKey:['sorting-capabilities'],queryFn:()=>get<{feature_enabled:boolean}>('sorting-capabilities')})
  const {pathname,search} = useLocation()
  const management=[...menus.slice(6,9),menus[9]]
  const active=management.some(([path])=>path===pathname)
  if(sorting.data?.feature_enabled)return <nav className="work-menu" aria-label="업무 메뉴"><details className="management-menu" open><summary>자료 등록·관리</summary><NavLink to="/lines">수출신고필증 등록</NavLink><NavLink to="/sorting-records">선별기록·증빙 등록</NavLink></details><NavLink to="/settlement-work">공동선별비 정산·제출</NavLink><NavLink to="/submission">회원사별 수출실적</NavLink><NavLink to="/archives">제출자료 보관함</NavLink><details className="management-menu" open={['/members','/contracts','/facilities','/support-budgets','/settings','/help'].includes(pathname)}><summary>기준정보·설정</summary><NavLink to="/members">회원사 명부</NavLink><NavLink to="/contracts">계약서 등록</NavLink><NavLink to="/facilities">공동선별장 명부</NavLink><NavLink to="/support-budgets">공동선별비 지원기준·배정</NavLink><NavLink to="/settings">조직·실적 기준·백업</NavLink><NavLink to="/help">사용 안내</NavLink></details></nav>
  return <nav className="work-menu" aria-label="업무 메뉴">
    <MenuLink item={menus[0]}/>
    <MenuLink item={menus[1]}/>
    <NavLink to="/contracts">계약서 등록</NavLink>
    {sorting.data?.feature_enabled&&<NavLink to="/facilities">공동선별장 명부</NavLink>}
    {sorting.data?.feature_enabled&&<NavLink to="/sorting-records">선별기록·증빙 등록</NavLink>}
    {[menus[2],menus[5]].map(item=><MenuLink key={item[0]} item={item}/>)}
    {sorting.data?.feature_enabled&&<NavLink to="/support-budgets">공동선별비 지원기준·배정</NavLink>}
    {sorting.data?.feature_enabled&&<NavLink to="/sorting">공동선별비 정산</NavLink>}
    {sorting.data?.feature_enabled&&<NavLink to="/monthly-reports">월별 정산·지급보고</NavLink>}
    <details className="management-menu" key={pathname+search} open={active}>
      <summary>설정·관리</summary>
      {management.map(item=><MenuLink key={item[0]} item={item}/>)}
    </details>
  </nav>
}

const referenceDescriptions:Record<string,string>={'/members':'회원사명·사업자등록번호·회원자격을 관리합니다. 등록된 회원사는 계약·수출실적·공동선별비 업무에서 함께 사용합니다.','/contracts':'생산자회원사와 수출업체의 계약 내용·기간·원본 PDF를 관리합니다.','/facilities':'공동선별장 시설과 지정 증빙을 관리합니다. 일별·월별 작업 자료는 선별기록·증빙 등록에서 입력하세요.','/support-budgets':'연도와 적용 기간별 지원단가·총예산·회원사 배정한도를 관리합니다. 수출실적 계산 기준과는 별개입니다.','/settings':'조직 정보와 연도별 수출실적 인정기준을 설정하고 데이터를 백업합니다. 공동선별비 기준은 지원기준·배정에서 설정하세요.','/help':'자료 등록, 실적 계산, 공동선별비 정산·제출 방법을 확인합니다.'}

function App() {
  const sorting=useQuery({queryKey:['sorting-capabilities'],queryFn:()=>get<{feature_enabled:boolean}>('sorting-capabilities')})
  const shutdown = useAction()
  const [exited,setExited] = useState(false)
  const location=useLocation()
  const office=useQuery({queryKey:['office'],queryFn:()=>get<{item:Org|null;choices:Org[]}>('office')})
  const queryClient=useQueryClient()
  const fields=useQuery({queryKey:['fields'],queryFn:()=>get<Record<'members'|'lines',Fields>>('fields')})
  const org=office.data?.item??undefined
  const refresh=(item:Org)=>queryClient.setQueryData(['office'],{item,choices:[]})
  const requiresOrg=(children:React.ReactNode)=>org ? children : <section className="panel"><h2>품목을 등록하세요</h2>
    <p>프로그램 설정에서 품목을 등록하면 업무 자료를 관리할 수 있습니다.</p><NavLink to="/settings">프로그램 설정</NavLink></section>
  if (exited) return <main className="content"><p role="status">프로그램이 종료되었습니다. 이 탭을 닫아 주세요.</p></main>
  return <WorkflowProvider key={org?.id??0} org={org?.id??0}><div className="layout"><aside><div className="sidebar-content"><div className="brand"><div><b>공동정산 수출실적</b><small>통합조직 업무시스템</small></div></div>
    <WorkMenu/>
    <footer><button className="exit-button danger" type="button" disabled={shutdown.busy} onClick={()=>{
      if (!window.confirm('프로그램을 종료하시겠습니까? 저장하지 않은 변경사항은 사라집니다.')) return
      void shutdown.run(async()=>{
        await send('shutdown')
        setExited(true)
        return ''
      })
    }}>{shutdown.busy?'종료 중…':'프로그램 종료'}</button>
    {shutdown.status&&!shutdown.busy&&<p role="alert">{shutdown.status}</p>}</footer></div></aside>
    <main><header><div>공동정산 수출실적 관리</div><span>품목 : <strong>{org?.name??'설정 필요'}</strong></span></header>
      <div className="content">{(office.isError||fields.isError) && <p role="alert">서버 연결을 확인하세요.</p>}
      {org&&sorting.data?.feature_enabled&&<WorkflowBar org={org.id}/>}<WorkflowRoute>{referenceDescriptions[location.pathname]&&<p className="panel reference-guidance">{referenceDescriptions[location.pathname]}</p>}<Routes>{menus.map(([path,label])=><Route key={path} path={path} element={<>
        <div className="page-title"><h1>{label}</h1></div>
        {path==='/'&&office.isSuccess&&!org?<Navigate to="/settings" replace/> :
          path==='/settings'?office.isSuccess&&<Settings org={org} choices={office.data.choices} refresh={refresh}/> : path==='/help'?<Help/> :
          requiresOrg(org && (path==='/'?(sorting.data?.feature_enabled?<WorkspaceIntro/>:<Dashboard org={org}/>) : path==='/members'||path==='/lines' ?
          (fields.data && <Suspense fallback={<p role="status">원장을 불러오는 중…</p>}>
            <LedgerGrid key={org.id+path} org={org.id} kind={path==='/members'?'members':'lines'}
              fields={fields.data[path==='/members'?'members':'lines']}/>
          </Suspense>) :
          path==='/submission'?<SubmissionWorkspace key={org.id} org={org} fields={fields.data}/> :
          <RunWorkspace key={org.id+path+location.search} org={org} mode={path.slice(1)} fields={fields.data}/>))}
      </>}/>)}{sorting.data?.feature_enabled&&<Route path="/sorting" element={requiresOrg(org&&<Suspense fallback={<p role="status">검토 화면을 불러오는 중…</p>}><SortingWorkspace key={org.id} org={org}/></Suspense>)}/>}{sorting.data?.feature_enabled&&<Route path="/facilities" element={<>
        <div className="page-title"><h1>공동선별장 명부</h1></div>
        {requiresOrg(org&&<Suspense fallback={<p role="status">시설 명부를 불러오는 중…</p>}><FacilityRegistry key={org.id} org={org}/></Suspense>)}
      </>}/>} {sorting.data?.feature_enabled&&<Route path="/sorting-records" element={requiresOrg(org&&<Suspense fallback={<p role="status">접수 자료를 불러오는 중…</p>}><SortingIntakeWorkspace key={org.id} org={org}/></Suspense>)}/>}{sorting.data?.feature_enabled&&<Route path="/support-budgets" element={requiresOrg(org&&<Suspense fallback={<p role="status">지원기준을 불러오는 중…</p>}><SupportBudgetWorkspace key={org.id} org={org}/></Suspense>)}/>}{sorting.data?.feature_enabled&&<Route path="/monthly-reports" element={requiresOrg(org&&<Suspense fallback={<p role="status">월별 보고를 불러오는 중…</p>}><MonthlyReportWorkspace key={org.id} org={org}/></Suspense>)}/>}<Route path="/contracts" element={<>
        <div className="page-title"><h1>계약서 등록</h1></div>
        {requiresOrg(org&&<Suspense fallback={<p role="status">계약 화면을 불러오는 중…</p>}><ContractsWorkspace key={org.id} org={org}/></Suspense>)}
      </>}/><Route path="/settlement-work" element={requiresOrg(org&&<><h1>공동선별비 정산·제출</h1><WorkbenchHome org={org}/></>)}/><Route path="/reference" element={<Navigate to="/settings" replace/>}/><Route path="/archives" element={requiresOrg(org&&<><h1>제출자료 보관함</h1><ArchiveWorkspace org={org.id}/></>)}/></Routes></WorkflowRoute></div>
    </main></div></WorkflowProvider>
}

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient()}><HashRouter><App/></HashRouter></QueryClientProvider>)
