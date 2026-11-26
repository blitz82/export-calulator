import {useEffect,useRef} from 'react'
import type {Catalog,Edge,Ref} from './settlement-model'
import {EvidenceEditor,SettlementPdf} from './settlement-records'
import {PdfView} from './pdf-view'
import {amountUnits,weightText,boardBalances} from './settlement-board-model'
import type {MemberSupport} from './support-budget-model'
import {money} from './support-budget-model'

type Props={org:number;catalog:Catalog;lines:Edge[];edge?:number;selection?:{side:'left'|'right';id:number};supportMembers?:MemberSupport[];onEdit:(key:keyof Edge,value:Edge[keyof Edge])=>void;onRemove:()=>void;onRecheck:()=>void;onPdf:(ref:Ref)=>void;onClose:()=>void}
export function SettlementInspector({org,catalog,lines,edge,selection,supportMembers,onEdit,onRemove,onRecheck,onPdf,onClose}:Props){
  const panel=useRef<HTMLElement>(null)
  useEffect(()=>{panel.current?.scrollTo({top:0})},[edge,selection?.side,selection?.id])
  const current=edge===undefined?undefined:lines[edge]
  const batch=catalog.batches.find(b=>b.id===(current?.batch_id??(selection?.side==='left'?selection.id:undefined)))
  const declaration=catalog.declarations.find(d=>d.id===(current?.declaration_id??(selection?.side==='right'?selection.id:undefined)))
  const balance=boardBalances(catalog.batches,catalog.declarations,lines)
  const support=supportMembers?.find(m=>m.member_id===batch?.data.producer?.id)
  return <aside ref={panel} className="panel board-inspector" data-active={!!current||!!selection} aria-label="연결 상세 패널">
    <div className="board-inspector-heading"><div><span className="board-eyebrow">DETAILS & EVIDENCE</span><h2>{current?'선택 연결 조정':batch?'선별기록 상세':declaration?'신고필증 상세':'연결 상세'}</h2></div>{(current||selection)&&<button type="button" aria-label="연결 상세 닫기" onClick={onClose}>×</button>}</div>
    {!batch&&!declaration&&<div className="board-empty"><span className="board-empty-icon">↔</span><b>연결할 기록을 선택하세요</b><p>카드를 선택하면 원본·물량을, 연결선을 선택하면 양쪽 사용량과 차이 근거를 확인할 수 있습니다.</p><ol><li>왼쪽 선별기록 선택</li><li>오른쪽 후보와 연결</li><li>차이·잔량 확인 후 확정</li></ol></div>}
    {batch&&<section className="board-detail-card"><span>선별기록</span><b>{batch.data.title??batch.data.external_id}</b><p>{batch.data.producer?.name??'생산자 미확인'}</p><p>{batch.data.start??batch.data.work_date} ~ {batch.data.end??batch.data.work_date}</p><strong>수출용 {batch.quantity} kg</strong><small>연결 후 잔량 {weightText(balance.left.get(batch.id)??0n)} kg</small>{batch.data.evidence?.[0]&&<button type="button" onClick={()=>onPdf(batch.data.evidence![0])}>선별 원본 PDF</button>}</section>}
    {declaration&&<section className="board-detail-card"><span>수출신고필증</span><b>{declaration.number} / {declaration.line}</b><p>제조자 {declaration.maker_name}</p><p>화주 {declaration.owner_name||'미입력'}<br/>대행 {declaration.agent_name||'미입력'}</p><p>선적일 {declaration.shipped||'미입력'} · 신고일 {declaration.declared||'미입력'}</p><strong>순중량 {declaration.kg} kg</strong><small>연결 후 잔량 {weightText(balance.right.get(declaration.id)??0n)} kg</small>{declaration.pdf?<PdfView org={org} pdf={declaration.pdf}/>:<small>등록된 원본 PDF가 없습니다.</small>}{declaration.errors.length>0&&<div className="board-detail-warning"><b>등록 정보 확인</b>{declaration.errors.map((e,i)=><p key={i}>{e}</p>)}</div>}</section>}
    {support&&<section className="board-detail-card budget-member-card"><span>이번 정산 · 생산자회원사 지원액</span><b>{support.name}</b><p>{support.status}</p><p>회원사 배정 잔액 {money(support.remaining)}원</p><strong>이번 반영 {money(support.amount)}원</strong><p>한도 미반영 {money(support.unfunded)}원 · 물량 잔량과 별도</p><small>이 회원사의 이번 작업 전체 연결을 합산한 금액입니다.</small></section>}
    {current&&<div className="settlement-form board-connection-editor"><label>선별 측 사용량 kg<input inputMode="decimal" value={current.left} onChange={e=>onEdit('left',e.target.value)}/></label><label>신고 측 사용량 kg<input inputMode="decimal" value={current.right} onChange={e=>onEdit('right',e.target.value)}/></label>
      <div className="full board-difference"><span>양쪽 중량 차이</span><b>{weightText(amountUnits(current.left)-amountUnits(current.right))} kg</b><small>참고 계산 적용 물량: {weightText(amountUnits(current.left)<amountUnits(current.right)?amountUnits(current.left):amountUnits(current.right))} kg</small>{(balance.left.get(current.batch_id)??0n)<0n||(balance.right.get(current.declaration_id)??0n)<0n?<p role="alert">가용 물량을 초과했습니다. 다른 연결을 포함한 사용량을 줄이세요.</p>:current.left!==current.right?<p>차이 사유·근거와 허용 기준을 확인하세요. 설명된 차이와 물량 초과는 별도로 검증합니다.</p>:null}</div>
      <label className="full">중량 차이 사유<textarea value={current.reason} onChange={e=>onEdit('reason',e.target.value)} placeholder="감모·계근·재포장 등 확인 내용"/></label><details className="full"><summary>추가 확인 사유</summary><label>허용 기준 밖 추가 확인 사유<textarea value={current.exception_reason} onChange={e=>onEdit('exception_reason',e.target.value)}/></label><label>기간·연도 귀속 확인 사유<textarea value={current.period_reason} onChange={e=>onEdit('period_reason',e.target.value)}/></label></details>
      <EvidenceEditor files={catalog.files} value={current.evidence} onChange={v=>onEdit('evidence',v)} onView={onPdf}/><button type="button" onClick={onRecheck}>현재 원본으로 다시 대조</button><button type="button" className="danger" onClick={onRemove}>이 연결 해제</button>
    </div>}
  </aside>
}

export function SettlementEvidenceDialog({path,catalog,value,onClose}:{path:string;catalog:Catalog;value:Ref;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null)
  useEffect(()=>{const element=dialog.current,previous=document.activeElement as HTMLElement|null;element?.showModal();return()=>{element?.close();previous?.focus()}},[])
  return <dialog ref={dialog} className="board-evidence-dialog" aria-label="선별 증빙 원문 대조" onCancel={e=>{e.preventDefault();onClose()}}><SettlementPdf path={path} files={catalog.files} value={value} onClose={onClose}/></dialog>
}
