import { lazy, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { HashRouter, Navigate, NavLink, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { download, get, send } from './api'
import type { Fields, Problem, Row } from './grid'
import { HelpTip } from './help-tip'
import './style.css'

const LedgerGrid = lazy(() => import('./grid').then(module => ({default:module.LedgerGrid})))
const menus = [
  ['/', '업무 홈', '품목의 등록 현황과 지금 할 일을 확인합니다.'],
  ['/members', '회원사 등록·수정', '회원사를 추가하거나 Excel 명부를 가져옵니다.'],
  ['/lines', '수출신고필증 입력·수정', '수출신고필증의 품목란을 입력하고 보완합니다.'],
  ['/calculate', '수출실적 계산', '연도별 정책을 적용해 직접·간접 수출실적을 계산합니다.'],
  ['/results', '회원사별 실적 확인', '회원사별 인정 금액과 신고 품목란별 근거를 확인합니다.'],
  ['/submission', '제출자료 준비', '선택 연도와 직전 연도의 제출용 Excel을 준비합니다.'],
  ['/audit', '감사 자료', '계산 근거, 원장 변경 이력 및 감사 증빙을 조회합니다.'],
  ['/checks', '상세 검증 이력', '이전 계산의 입력 및 계산 문제를 확인합니다.'],
  ['/settings', '프로그램 설정', '품목과 연도별 계산 정책을 등록하고 데이터 백업을 내려받습니다.'],
  ['/help', '도움말', '공동정산 처리 순서와 실적 인정 기준을 확인합니다.'],
] as const
type Org = {id:number; name:string}
type Run = {id:number; year:number; time:string}
type Detail = {accepted_lines:number; actual_usd:string; attributed_usd:string; actual_kg:string|null; ready:boolean; rule:string;
  summary:Row[]; ledger:Row[]; checks:Problem[]; member_issues:Problem[]}
type Readiness = {year:number; cutoff:string; previous_cutoff:string; saved:boolean; blocker:string|null}
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

function Table({rows,fields,onPick,searchLabel='표 검색'}: {rows: Row[]; fields: Fields; onPick?: (row:Row) => void; searchLabel?:string}) {
  const [search,setSearch] = useState('')
  const [sort,setSort] = useState<{key:string;reverse:boolean}>()
  const [page,setPage] = useState(0)
  const items = rows.filter(r => Object.keys(fields).some(k => String(r[k] ?? '').toLowerCase().includes(search.toLowerCase())))
  if (sort) items.sort((a,b) => (sort.reverse?-1:1)*(numericKey(sort.key) ?
    Number(a[sort.key]??0)-Number(b[sort.key]??0) : String(a[sort.key]??'').localeCompare(String(b[sort.key]??''),'ko',{numeric:true})))
  const last = Math.max(0, Math.ceil(items.length/100)-1)
  const active = Math.min(page,last)
  return <section className="panel">
    <div className="toolbar"><label>{searchLabel}<input type="search" value={search}
      onChange={e => {setSearch(e.target.value); setPage(0)}}/></label><span className="muted">{items.length.toLocaleString('ko-KR')}건</span></div>
    <div className="table-scroll"><table><thead><tr>{Object.entries(fields).map(([key,label]) => <th key={key} className={numericKey(key)?'numeric':undefined}>
      <button onClick={() => setSort({key,reverse:sort?.key===key && !sort.reverse})}>{label}{sort?.key===key ? sort.reverse?' (내림차순)':' (오름차순)':''}</button></th>)}
      {onPick && <th>상세</th>}</tr></thead><tbody>{items.slice(active*100,active*100+100).map((row,i) => <tr key={i}>
      {Object.keys(fields).map(key => <td key={key} className={numericKey(key)?'numeric':undefined}>{numericKey(key)?formatNumber(row[key]):String(row[key]??'')}</td>)}
      {onPick && <td><button onClick={() => onPick(row)}>보기</button></td>}</tr>)}</tbody></table></div>
    {!items.length && <p className="muted">조회된 자료가 없습니다.</p>}
    {last > 0 && <div className="toolbar"><button disabled={!active} onClick={() => setPage(active-1)}>이전</button>
      <span>{active+1} / {last+1}</span><button disabled={active===last} onClick={() => setPage(active+1)}>다음</button></div>}
  </section>
}

function Dashboard({org}: {org:Org}) {
  const prefix = `org/${org.id}`
  const members = useQuery({queryKey:[prefix+'/data/members'],queryFn:()=>get<Row[]>(prefix+'/data/members')})
  const lines = useQuery({queryKey:[prefix+'/data/lines'],queryFn:()=>get<Row[]>(prefix+'/data/lines')})
  const runs = useQuery({queryKey:[prefix+'/runs'],queryFn:()=>get<Run[]>(prefix+'/runs')})
  const checksPath=prefix+'/line-checks'
  const checks=useQuery({queryKey:[checksPath],queryFn:()=>get<Record<string,Problem[]>>(checksPath)})
  const count=checks.data && Object.values(checks.data).filter(issues=>issues.some(p=>p.severity!=='INFO')).length
  const latest=runs.data?.[0]
  const detail=useQuery({queryKey:[prefix,'run',String(latest?.id??'')],
    queryFn:()=>get<Detail>(`${prefix}/runs/${latest?.id}`),enabled:!!latest})
  const policies=useQuery({queryKey:[prefix+'/policies'],queryFn:()=>get<Row[]>(prefix+'/policies')})
  const next = !members.data || !lines.data || !runs.data || count===undefined || !policies.data || (latest&&!detail.data)
    ? undefined
    : !members.data.length ? ['/members','회원사 명부를 등록하세요.','회원사 등록하기']
    : !lines.data.length ? ['/lines','수출신고필증을 입력하세요.','신고필증 입력하기']
    : count > 0 ? ['/lines?filter=issues','보완이 필요한 신고필증을 확인하세요.','보완 원장 열기']
    : !policies.data.length ? ['/settings#calculation-policy','수출실적을 계산할 연도의 정책을 등록하세요.','계산 정책 열기']
    : !latest ? ['/calculate','등록한 자료로 수출실적을 계산하세요.','수출실적 계산하기']
    : !detail.data?.ready ? [`/calculate?run=${latest.id}`,'최근 계산에서 보완할 항목을 확인하세요.','계산 검증 열기']
    : ['/results','회원사별 실적을 확인한 뒤 제출자료를 준비하세요.','회원사별 실적 확인']
  return <>
    <section className="panel next-action"><h2>지금 할 일</h2>
      {next ? <><p>{next[1]}</p><NavLink className="button primary" to={next[0]}>{next[2]}</NavLink></>
        : <p role="status">{[members,lines,runs,checks,policies,detail].some(query=>query.isError)
          ? '업무 현황을 불러오지 못했습니다.' : '업무 현황을 불러오는 중…'}</p>}
    </section>
    <div className="metrics">
      <NavLink className="metric metric-link" to="/lines?filter=issues"><span>보완 필요 원장 행</span>
        <strong>{count?.toLocaleString('ko-KR')??'—'}</strong> <small>행 · 원장 열기</small></NavLink>
      <NavLink className="metric metric-link" to={latest?`/calculate?run=${latest.id}`:'/calculate'}><span>최근 계산</span>
        <strong>{latest?`${latest.year}년`:'—'}</strong><small className="metric-detail">{latest?(detail.data?.ready?'검증 통과':detail.data?'보완 필요':'검증 상태 확인'):'계산 기록 없음'}</small></NavLink>
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
  const action=useAction()
  const prefix = `org/${org?.id}`
  const policies = useQuery({queryKey:[prefix+'/policies'],queryFn:()=>get<Row[]>(prefix+'/policies'),enabled:!!org})
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
      await download('backup'); return '백업 다운로드 완료'
    })}>백업 파일 다운로드</button></section>
    <p className="status" role="status">{action.status}</p>
  </>
}

function RunWorkspace({org,mode,fields}: {org:Org;mode:string;fields?:Record<'members'|'lines',Fields>}) {
  const prefix = `org/${org.id}`
  const [year,setYear]=useState(mode==='submission'?'':String(new Date().getFullYear()))
  const [params]=useSearchParams()
  const [runId,setRunId]=useState(params.get('run')??'')
  const [number,setNumber]=useState('')
  const [rowId,setRowId]=useState<number>()
  const action=useAction()
  const runs=useQuery({queryKey:[prefix+'/runs'],queryFn:()=>get<Run[]>(prefix+'/runs')})
  const current=runId || String(runs.data?.[0]?.id ?? '')
  const detail=useQuery({queryKey:[prefix,'run',current],queryFn:()=>get<Detail>(`${prefix}/runs/${current}`),enabled:!!current && mode!=='submission'})
  const policies=useQuery({queryKey:[prefix+'/policies'],queryFn:()=>get<{year:number}[]>(prefix+'/policies'),enabled:mode==='submission'})
  const years=[...new Set([...(policies.data??[]),...(runs.data??[])].map(item=>item.year))].sort((a,b)=>b-a)
  const submissionYear=years.includes(Number(year))?Number(year):years[0]
  const annualRuns=runs.data?.filter(run=>run.year===submissionYear)??[]
  const selectedRun=annualRuns.find(run=>String(run.id)===runId)??annualRuns[0]
  const previousYear=submissionYear===undefined?undefined:submissionYear-1
  const previousRun=runs.data?.find(run=>run.year===previousYear)
  const hasPolicies=[submissionYear,previousYear].every(value=>policies.data?.some(policy=>policy.year===value))
  const annualPath=prefix+'/annual-submission?run_id='+selectedRun?.id+'&previous_id='+previousRun?.id
  const readiness=useQuery({queryKey:[annualPath],queryFn:()=>get<Readiness>(annualPath),
    enabled:mode==='submission'&&!!selectedRun&&!!previousRun})
  const history=useQuery({queryKey:[prefix,'history',rowId],queryFn:()=>get<Row[]>(prefix+'/audit'+(rowId?'?row_id='+rowId:'')),
    enabled:mode==='audit'||!!rowId})
  const evidence=useQuery({queryKey:[prefix,'evidence',current,rowId],queryFn:()=>get<{row:Row;source:Row|null;decisions:Row[]}>(`${prefix}/runs/${current}/evidence/${rowId}`),
    enabled:!!rowId && !!current})
  const data=detail.data
  const chooser=(value:string,change:(value:string)=>void, label:string, filter?:number)=>
    <label><span>{label} <HelpTip text="계산할 때마다 생성되는 번호입니다. 선택한 실행 당시의 계산 및 검증 결과를 조회합니다."/></span><select aria-label={label} value={value} onChange={e=>change(e.target.value)}><option value="">선택</option>
      {runs.data?.filter(r=>!filter||r.year===filter).map(r=><option key={r.id} value={r.id}>{mode==='audit'
        ? `#${r.id} · ${r.time.slice(0,10)}` : `${r.year}년 · #${r.id} · ${r.time}`}</option>)}</select></label>
  if (mode==='submission') {
    const result=readiness.data
    const canFinalize=!!result&&!result.blocker&&!readiness.isFetching
    return <section className="panel submission-panel">
      <h2>연도별 공동정산 수출실적</h2>
      <p className="muted">품목 {org.name}</p>
      <div className="toolbar">
        <label><span>실적 연도 <HelpTip text="수출실적을 조회하거나 계산할 연도입니다."/></span><select aria-label="실적 연도" value={submissionYear??''} onChange={e=>{setYear(e.target.value);setRunId('')}}>
          {!years.length&&<option value="">정책 등록 필요</option>}
          {years.map(value=><option key={value} value={value}>{value}년</option>)}
        </select></label>
        <button className={canFinalize?undefined:'primary'} disabled={action.busy||!hasPolicies} onClick={()=>action.run(async()=>{
          const prior=await send<{ready:boolean}>(prefix+'/runs?year='+previousYear)
          const run=await send<{id:number;ready:boolean}>(prefix+'/runs?year='+submissionYear)
          await runs.refetch()
          setYear(String(submissionYear))
          setRunId(String(run.id))
          return prior.ready&&run.ready?'두 연도 계산을 완료했습니다.':'계산 결과를 검토하세요.'
        })}>두 연도 계산</button>
      </div>
      <p>제출자료에는 선택한 연도와 직전 연도의 실적이 포함됩니다. 자료를 수정했다면 두 연도를 다시 계산하세요.</p>
      {annualRuns.length>0&&<details className="compact"><summary>계산 기록 선택</summary>
        <div className="toolbar">{chooser(String(selectedRun?.id??''),setRunId,'계산 실행번호',submissionYear)}</div>
      </details>}
      {submissionYear!==undefined&&<p>출력 연도 <b>{previousYear}년 · {submissionYear}년</b></p>}
      {submissionYear!==undefined&&!hasPolicies&&<p><NavLink to="/settings#calculation-policy">두 연도의 계산 정책을 등록하세요.</NavLink></p>}
      {hasPolicies&&(!selectedRun||!previousRun)&&<p>제출 전에 두 연도를 계산하세요.</p>}
      {result&&<p>회원 기준일 <b>{previousYear}년 {result.previous_cutoff} · {submissionYear}년 {result.cutoff}</b> · {result.saved?'확정본 보관됨':result.blocker??'확정 가능'}</p>}
      {result?.blocker&&<div className="toolbar">
        <NavLink to={'/calculate?run='+selectedRun?.id}>{submissionYear}년 검증 열기</NavLink>
        <NavLink to={'/calculate?run='+previousRun?.id}>{previousYear}년 검증 열기</NavLink>
      </div>}
      {!years.length&&!policies.isLoading&&<NavLink to="/settings#calculation-policy">연도별 정책 등록</NavLink>}
      <div className="toolbar">
        <button disabled={!selectedRun||!previousRun||action.busy} onClick={()=>action.run(async()=>{
          await download(annualPath,'POST')
          return '초안 다운로드 완료'
        })}>초안 다운로드</button>
        <button className="primary" disabled={!canFinalize||action.busy} onClick={()=>{
          if (!result?.saved&&!window.confirm('선택 연도의 제출자료를 확정하시겠습니까?')) return
          void action.run(async()=>{
            try { await download(annualPath+'&final=true','POST') }
            finally { void readiness.refetch() }
            return '확정본 다운로드 완료'
          })
        }}>{result?.saved?'제출용 Excel 다운로드':'최종 확정 후 제출용 Excel 다운로드'}</button>
      </div>
      <p role="status">{action.status||(readiness.isError?(readiness.error as Error).message:'')}</p>
    </section>
  }
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
      {mode==='results'&&<div className="toolbar"><NavLink className="button primary" to="/submission">제출자료 준비</NavLink></div>}
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
        ['신고 기준 순중량',data.actual_kg,'kg'],['검증 통과 품목란',data.accepted_lines,'건']].map(([label,value,unit]) =>
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
        onPick={r=>setRowId(r.row_id ? Number(r.row_id) : undefined)}/> : <>
        <Table rows={data.summary} fields={summaryFields} onPick={r=>{setNumber(String(r.number));setRowId(undefined)}}/>
        <details className="compact" open={!!number || mode==='audit'} key={number||'all'}><summary>신고 품목란별 인정 내역</summary>
        <div className="section-top"><h2 className="recognition-heading">{number ? String(data.summary.find(row=>row.number===number)?.name ?? number) : '전체 회원사'}</h2>
          {number && <button onClick={()=>{setNumber('');setRowId(undefined)}}>뒤로</button>}</div>
        <Table rows={data.ledger.filter(r=>!number||r.number===number)} fields={ledgerFields} onPick={r=>setRowId(Number(r.row_id))}/>
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
    <li><NavLink to="/members">회원사 등록·수정</NavLink>에서 명부를 등록합니다. 역할 사업자는 사업자번호와 등록 이름 또는 별칭으로 대조합니다.</li>
    <li><NavLink to="/lines">수출신고필증 입력·수정</NavLink>에서 실제 품목란, 정정 버전, 금액, 물량 및 역할을 입력합니다. 원본에 역할 정보가 없으면 확인 상태를 명시합니다.</li>
    <li>실적 계산을 실행하고 검증 목록을 확인합니다. 정정 버전 충돌은 원본과 적용 범위를 확인한 뒤 정리합니다.</li>
    <li>회원사별 실적을 확인한 뒤 <NavLink to="/submission">제출자료 준비</NavLink>에서 실적 연도를 선택하고 두 연도를 계산합니다. 검증을 통과하면 제출용 Excel을 내려받습니다.</li>
  </ol><h2>인정 기준</h2><p>대행자와 화주는 직접수출 자격, 제조자는 간접수출 자격을 가집니다. 같은 사업자가 두 자격을 갖는 경우 직접수출을 한 번 인정합니다.</p>
  <p>동일 신고 품목란의 실제 수출액은 한 번 집계합니다. 서로 다른 회원사에는 해당 역할에 따른 실적이 각각 귀속됩니다.</p></section>
}

function MenuLink({item:[path,label,description]}: {item:typeof menus[number]}) {
  return <NavLink to={path} end aria-description={description}>{label}</NavLink>
}

function WorkMenu() {
  const {pathname,search} = useLocation()
  const management=menus.slice(6,9)
  const active=management.some(([path])=>path===pathname)
  return <nav className="work-menu" aria-label="업무 메뉴">
    <MenuLink item={menus[0]}/>
    <div role="group" aria-labelledby="entry-menu" className="menu-group">
      <span id="entry-menu">자료 입력</span>
      {menus.slice(1,3).map(item=><MenuLink key={item[0]} item={item}/>)}
    </div>
    <div role="group" aria-labelledby="results-menu" className="menu-group">
      <span id="results-menu">실적 계산·확인</span>
      {menus.slice(3,5).map(item=><MenuLink key={item[0]} item={item}/>)}
    </div>
    <MenuLink item={menus[5]}/>
    <details className="management-menu" key={pathname+search} open={active}>
      <summary>설정·관리</summary>
      {management.map(item=><MenuLink key={item[0]} item={item}/>)}
    </details>
    <MenuLink item={menus[9]}/>
  </nav>
}

function App() {
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
  return <div className="layout"><aside><div className="brand"><div><b>공동정산 수출실적</b><small>통합조직 업무시스템</small></div></div>
    <WorkMenu/>
    <footer><button className="exit-button danger" type="button" disabled={shutdown.busy} onClick={()=>{
      if (!window.confirm('프로그램을 종료하시겠습니까? 저장하지 않은 변경사항은 사라집니다.')) return
      void shutdown.run(async()=>{
        await send('shutdown')
        setExited(true)
        return ''
      })
    }}>{shutdown.busy?'종료 중…':'프로그램 종료'}</button>
    {shutdown.status&&!shutdown.busy&&<p role="alert">{shutdown.status}</p>}</footer></aside>
    <main><header><div>공동정산 수출실적 관리</div><span>품목 : <strong>{org?.name??'설정 필요'}</strong></span></header>
      <div className="content">{(office.isError||fields.isError) && <p role="alert">서버 연결을 확인하세요.</p>}
      <Routes>{menus.map(([path,label])=><Route key={path} path={path} element={<>
        <div className="page-title"><h1>{label}</h1></div>
        {path==='/'&&office.isSuccess&&!org?<Navigate to="/settings" replace/> :
          path==='/settings'?office.isSuccess&&<Settings org={org} choices={office.data.choices} refresh={refresh}/> : path==='/help'?<Help/> :
          requiresOrg(org && (path==='/'?<Dashboard org={org}/> : path==='/members'||path==='/lines' ?
          (fields.data && <Suspense fallback={<p role="status">원장을 불러오는 중…</p>}>
            <LedgerGrid key={org.id+path} org={org.id} kind={path==='/members'?'members':'lines'}
              fields={fields.data[path==='/members'?'members':'lines']}/>
          </Suspense>) :
          <RunWorkspace key={org.id+path+location.search} org={org} mode={path.slice(1)} fields={fields.data}/>))}
      </>}/>)}</Routes></div>
    </main></div>
}

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient()}><HashRouter><App/></HashRouter></QueryClientProvider>)
