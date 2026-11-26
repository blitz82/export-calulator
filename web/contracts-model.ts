export type Party={id:number;name:string;number:string;kind?:string}
export type AgreementData={
  title:string;kind:string;producer_id:number|null;exporter_id:number|null;document_id:number;parent_id:number|null;
  signed:string;start:string;end:string;auto_renew:boolean;terminated:string;product:string;contract_year:number;period_type:'연간 계약'|'기간 지정';
  quantity:string;unit:string;amount:string;currency:string;supply:string;export:string;settlement:string;
  payment:string;costs:string;special:string;memo:string;evidence_pages:Record<string,number>;review:string;
  producer?:Party;exporter?:Party
}
export type Agreement={id:number;revision:number;created:string;updated:string;producer_id:number|null;exporter_id:number|null;
  document_id:number;parent_id:number|null;data:AgreementData;state:string;status_issues:string[];same_business:boolean;warnings?:string[]}
export type ContractDocument={id:number;name:string;sha:string;created:string;pages:number;duplicate?:boolean;
  extraction:{method:string;pages:{page:number;text:string;warning:string}[];candidates:{field:string;value:string;page:number;confirmed:false}[]}}
export const evidenceLabels:Record<string,string>={parties:'계약 당사자',signed:'체결일',period:'계약 기간',product:'대상 품목',
  supply:'공급 약정',export:'수출 약정',settlement:'공동정산 약정',payment:'대금 지급',costs:'비용 부담',special:'특약'}
export function localToday(){
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
}
export function emptyAgreement():AgreementData{
  const year=Number(localToday().slice(0,4))
  return {title:'',kind:'공급·수출 계약',producer_id:null,exporter_id:null,document_id:0,parent_id:null,signed:'',start:`${year}-01-01`,end:`${year}-12-31`,contract_year:year,period_type:'연간 계약',
    auto_renew:false,terminated:'',product:'',quantity:'',unit:'',amount:'',currency:'',supply:'',export:'',settlement:'',payment:'',
    costs:'',special:'',memo:'',evidence_pages:{},review:'미검토'}
}
