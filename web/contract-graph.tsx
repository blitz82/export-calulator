import {useMemo,useState} from 'react'
import type {Agreement,Party} from './contracts-model'

type Connection={key:string;producer:Party;exporter:Party;contracts:Agreement[]}
export function ContractGraph({contracts,onOpen}:{contracts:Agreement[];onOpen:(contract:Agreement)=>void}){
  const [selected,setSelected]=useState<{role:string;id:number}>(),[edge,setEdge]=useState(''),[zoom,setZoom]=useState(1),[reverse,setReverse]=useState(false)
  const [hovered,setHovered]=useState<{role:string;id:number}>()
  const {connections,producers,exporters}=useMemo(()=>{
    const groups=new Map<string,Connection>()
    for(const row of contracts){
      if(row.same_business||!row.data.producer||!row.data.exporter)continue
      const producer=row.data.producer,exporter=row.data.exporter,key=producer.id+':'+exporter.id
      if(!groups.has(key))groups.set(key,{key,producer,exporter,contracts:[]})
      groups.get(key)!.contracts.push(row)
    }
    const connections=[...groups.values()]
    const parties=(role:'producer'|'exporter')=>[...new Map(connections.map(c=>[c[role].id,c[role]])).values()]
      .sort((a,b)=>(reverse?-1:1)*(a.name.localeCompare(b.name,'ko')||a.id-b.id))
    return {connections,producers:parties('producer'),exporters:parties('exporter')}
  },[contracts,reverse])
  const height=Math.max(producers.length,exporters.length,1)*84+65
  const position=(party:Party,role:'producer'|'exporter')=>40+(role==='producer'?producers:exporters).findIndex(p=>p.id===party.id)*84
  const validSelection=selected&&connections.some(c=>c[selected.role as 'producer'|'exporter'].id===selected.id)?selected:undefined
  const matches=(c:Connection)=>!validSelection||c[validSelection.role as 'producer'|'exporter'].id===validSelection.id
  const active=hovered&&connections.some(c=>c[hovered.role as 'producer'|'exporter'].id===hovered.id)?hovered:validSelection
  const highlighted=(c:Connection)=>!active||c[active.role as 'producer'|'exporter'].id===active.id
  const visibleEdge=connections.find(c=>c.key===edge)
  const focused=active?connections.filter(highlighted):[]
  function pick(role:string,id:number){setSelected(selected?.role===role&&selected.id===id?undefined:{role,id});setEdge('')}
  return <section className="panel">
    <h2>회원사 연결도</h2><p>선은 등록된 계약 관계입니다. 실제 수출실적·대금 지급·거래 이행을 뜻하지 않습니다. 동일 사업자의 자기 연결은 생략합니다.</p>
    <div className="toolbar"><button type="button" onClick={()=>setReverse(!reverse)}>이름 {reverse?'오름차순':'내림차순'} 정렬</button>
      <button type="button" disabled={zoom<=0.5} onClick={()=>setZoom(Math.max(.5,zoom-.25))}>축소</button><span>{Math.round(zoom*100)}%</span>
      <button type="button" disabled={zoom>=2} onClick={()=>setZoom(Math.min(2,zoom+.25))}>확대</button>
      <button type="button" onClick={()=>setZoom(1)}>화면 맞춤</button><button type="button" onClick={()=>{setSelected(undefined);setEdge('')}}>선택 해제</button>
    </div>
    {!connections.length?<p>표시할 업체 간 계약이 없습니다. 계약을 등록하거나 상태·기준일 필터를 변경하세요.</p>:<>
      <div className="contract-graph-scroll" tabIndex={0} aria-label="회원사 계약 연결도 확대 영역">
        <svg role="group" aria-label="생산자회원사와 수출업체의 계약 관계" viewBox={`0 0 1000 ${height}`} style={{width:zoom*100+'%',minWidth:650*zoom}}>
          <text x="20" y="23" className="contract-role">생산자회원사</text><text x="730" y="23" className="contract-role">수출업체</text>
          {connections.map(c=>{
            const y1=position(c.producer,'producer')+27,y2=position(c.exporter,'exporter')+27
            const path=`M 270 ${y1} C 465 ${y1}, 535 ${y2}, 730 ${y2}`
            const uncertain=c.contracts.some(r=>r.state==='확인 필요'||r.status_issues.length>0)
            return <g key={c.key} role="button" tabIndex={0} aria-label={`${c.producer.name}와 ${c.exporter.name} 계약 ${c.contracts.length}건`} aria-pressed={edge===c.key}
              opacity={highlighted(c)?1:.15} onClick={()=>setEdge(c.key)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setEdge(c.key)}}} className="contract-edge">
              <path d={path} stroke="transparent" strokeWidth="16" fill="none"/>
              <path d={path} stroke={edge===c.key||(active&&highlighted(c))?'#174f36':'#62766a'} strokeWidth={edge===c.key?2:active&&highlighted(c)?1.5:1} strokeDasharray={uncertain?'5 4':undefined} fill="none"/>
              <text x="500" y={(y1+y2)/2-5} textAnchor="middle" className="contract-edge-label">{c.contracts.length}건{uncertain?' · 확인 필요':''}</text>
              <title>{c.contracts.map(r=>`${r.data.title} · ${r.state}`).join('\n')}</title>
            </g>
          })}
          {(['producer','exporter'] as const).flatMap(role=>(role==='producer'?producers:exporters).map(p=>{
            const related=!active||focused.some(c=>c[role].id===p.id),source=active?.role===role&&active.id===p.id
            const emphasized=!!active&&related,x=role==='producer'?20:730,y=position(p,role)
            return <g key={role+p.id} role="button" tabIndex={0} aria-label={`${role==='producer'?'생산자회원사':'수출업체'} ${p.name} 연결 강조`}
              aria-pressed={selected?.role===role&&selected.id===p.id} className="contract-node" opacity={related?1:.25}
              onMouseEnter={()=>setHovered({role,id:p.id})} onMouseLeave={()=>setHovered(undefined)}
              onClick={()=>pick(role,p.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(role,p.id)}}}>
              <rect x={x} y={y} width="250" height="55" rx="3" fill={source?'#b7ddc4':emphasized?'#ddf0e3':'#fff'} stroke={emphasized?'#246044':'#62766a'} strokeWidth={emphasized?2:1}/>
              <text x={x+12} y={y+23}>{p.name.length>17?p.name.slice(0,16)+'…':p.name}</text>
              <text x={x+12} y={y+43} className="contract-number">{p.number||'사업자번호 미확인'}</text><title>{p.name} · {p.number}</title>
            </g>
          }))}
        </svg>
      </div>
      <p className="muted">업체 칸에 마우스를 올리면 계약 상대와 연결선이 강조됩니다. 클릭하면 강조가 유지됩니다. 실선: 등록 관계 · 점선: 확인 필요. 키보드로도 선택할 수 있습니다.</p>
      {visibleEdge&&<section className="contract-edge-detail" aria-label="선택한 계약 관계"><h3>{visibleEdge.producer.name} ↔ {visibleEdge.exporter.name}</h3>
        {visibleEdge.contracts.map(r=><article key={r.id}><b>{r.data.title}</b><p>{r.data.product} · {r.data.start} ~ {r.data.end||'종료일 미정'} · {r.state} · {r.data.review}</p>
          <p>{[r.data.supply,r.data.export,r.data.settlement,r.data.payment,r.data.costs].filter(Boolean).join(' / ')||'주요 약정 미입력'}</p>
          <button type="button" onClick={()=>onOpen(r)}>계약 상세·PDF 열기</button></article>)}
      </section>}
      <h3>연결 관계 표</h3><div className="table-scroll"><table><thead><tr><th>생산자회원사</th><th>수출업체</th><th>계약 건수</th><th>계약 확인</th></tr></thead>
        <tbody>{connections.filter(matches).map(c=><tr key={c.key}><td>{c.producer.name} · {c.producer.number}</td><td>{c.exporter.name} · {c.exporter.number}</td><td>{c.contracts.length}</td>
          <td>{c.contracts.map(r=><button key={r.id} type="button" onClick={()=>onOpen(r)}>{r.data.title} · {r.state}</button>)}</td></tr>)}</tbody></table></div>
    </>}
  </section>
}
