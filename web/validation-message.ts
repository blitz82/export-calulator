const labels:Record<string,string>={reviews:'회원사 내부검토',payments:'실제 지급',resolutions:'지출결의',declaration_evidence:'수출신고필증 원본',evidence:'근거 증빙',documents:'첨부 자료',allocations:'회원사 배분',file:'원본 PDF',page:'근거 페이지',member_id:'회원사',amount:'금액',decision:'내부결정액',date:'일자',start:'시작일',end:'종료일',title:'제목',writer:'작성자',destination:'제출처',reason:'변경·정정 사유',unit_price:'지원단가'}
export function validationMessage(detail:unknown){
  if(typeof detail==='string')return detail
  if(!Array.isArray(detail))return '요청을 완료하지 못했습니다.'
  const messages=detail.map(item=>{const loc=Array.isArray(item?.loc)?item.loc.filter((v:unknown)=>typeof v==='string'&&v!=='body'):[];const field=loc.at(-1) as string|undefined;const prefix=loc.slice(0,-1).map((k:string)=>labels[k]).filter(Boolean).join(' · ');const advice=field==='file'?'원본 PDF를 선택하세요.':field==='member_id'?'대상 회원사를 선택하세요.':field==='page'?'PDF의 실제 근거 페이지를 입력하세요.':`${labels[field??'']??'필수 항목'}의 입력값을 확인하세요.`;return prefix?prefix+': '+advice:advice})
  return [...new Set(messages)].slice(0,3).join(' / ')||'필수 입력 항목을 확인하세요.'
}
