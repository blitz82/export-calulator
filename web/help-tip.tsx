import { useId } from 'react'

export function HelpTip({text}: {text:string}) {
  const id=useId()
  return <span className="help-tip" tabIndex={0} aria-label="도움말" aria-describedby={id}>
    <span className="help-mark" aria-hidden="true">!</span>
    <span className="help-popup" id={id} role="tooltip">{text}</span>
  </span>
}

export const dateInputHelp = '하이픈(-)을 넣거나 빼도 같은 날짜로 처리합니다. 예: 2026-05-12 또는 20260512. 하이픈 없이 입력할 때는 숫자 8자리로 입력하세요.'

export const fieldHelp: Record<string,string> = {
  id:'프로그램이 저장할 때 부여하는 행 식별번호입니다.',
  name:'회원사 명부에 사용할 업체의 정식 명칭입니다.',
  kind:'생산자, 수출업체 또는 두 유형에 모두 해당하는 회원인지 선택합니다.',
  applied:'회원 가입을 신청한 날짜입니다.',
  approved:'회원 가입이 승인된 날짜입니다. 회원자격 기준일과 비교합니다.',
  withdrawn:'회원자격이 종료된 날짜입니다. 탈퇴하지 않은 경우 비워 둡니다.',
  aliases:'신고서에서 사용하는 다른 업체 표기를 입력합니다. 여러 개는 |로 구분합니다.',
  address:'제출양식에 기재할 회원사 주소입니다.',
  representative:'사업체 대표자 이름입니다.',
  contact:'회원사에서 공동정산을 담당하는 사람의 이름입니다.',
  phone:'공동정산 담당자에게 연락할 수 있는 전화번호입니다.',
  email:'공동정산 담당자의 이메일 주소입니다.',
  corporate:'법인등록번호를 알고 있을 때 입력합니다.',
  note:'회원사 관리에 필요한 참고사항을 기록합니다.',
  line:'신고필증에 표시된 실제 품목란 번호입니다. Excel 행 번호와 구분합니다.',
  version:'같은 신고가 정정되었을 때 정정본을 구분하는 양의 정수입니다.',
  declared:`수출신고일입니다. 귀속 기준이 신고일이면 이 날짜의 연도로 집계합니다. ${dateInputHelp}`,
  shipped:`선적일입니다. 귀속 기준이 선적일이면 이 날짜의 연도로 집계합니다. ${dateInputHelp}`,
  accepted:'수출신고가 수리된 날짜입니다. 원본에 있을 때 입력합니다.',
  country:'원본에 표시된 수출 대상 국가입니다.',
  hs:'품목의 HS코드 10자리입니다. 맨 앞의 0을 포함합니다.',
  product:'원본 신고필증에 적힌 품목명입니다.',
  usd:'신고 품목란의 달러 금액입니다. 직접·간접 수출실적을 계산할 때 사용합니다.',
  fob:'원본에 별도로 표시된 FOB 달러 금액입니다.',
  kg:'원본의 순중량을 kg으로 입력합니다. 비워 두면 관련 중량 합계는 미집계로 표시합니다.',
  quantity:'신고필증에 표시된 수량입니다. 수량단위를 함께 확인하세요.',
  unit:'입력한 수량의 단위입니다. 예를 들어 상자 또는 개를 입력합니다.',
  scope:'이 품목란이 공동정산 대상인지 확인해 선택합니다.',
  status:'수출신고의 유효, 취소 또는 미확정 상태를 선택합니다.',
}

export function ledgerHelp(key:string, kind:'lines'|'members') {
  if (key==='number') return kind==='lines'
    ? '원본 수출신고필증의 신고번호입니다. 품목란 번호와 함께 중복을 확인합니다.'
    : '사업자등록증의 숫자 10자리입니다. 동일 품목 안에서 중복을 확인합니다.'
  const role = key.startsWith('agent')?'수출대행자':key.startsWith('owner')?'수출화주':'제조자'
  if (['agent','owner','maker'].some(value=>key.startsWith(value))) {
    if (key.endsWith('_name')) return `신고필증에 기재된 ${role}의 업체명입니다. 회원사명 또는 등록 별칭과 대조합니다.`
    if (key.endsWith('_state')) return `${role}의 확인 상태입니다. 원본에 정보가 없으면 ‘원본에 없음’을, 비회원 업체는 업체명과 사업자번호를 입력한 뒤 ‘회원사 아님’을 선택합니다.`
    return `신고필증에 기재된 ${role}의 사업자번호입니다. 회원사 식별과 실적 귀속에 사용합니다.`
  }
  return fieldHelp[key]
}
