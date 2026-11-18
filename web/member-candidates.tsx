import type { Row } from './grid'

export const normalizeNumber = (value:unknown) => String(value??'').normalize('NFKC').replace(/[\s-]/g,'')
export const normalizeCompany = (value:unknown) => String(value??'').normalize('NFKC').replace(/\s+|주식회사|\(주\)/g,'').toLowerCase()
export const memberNames = (row:Row) => [row.name,...String(row.aliases??'').split('|')].map(normalizeCompany).filter(Boolean)

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
export function findMemberCandidates(members:Row[] | undefined, input:string, field:'name' | 'number', producersOnly=false) {
  const query = field==='name' ? normalizeCompany(input) : normalizeNumber(input)
  if (!query || (field==='number' && !/^\d{1,10}$/.test(query))) return []
  return (members ?? []).flatMap(member=>{
    const number = normalizeNumber(member.number)
    if (!/^\d{10}$/.test(number) || (producersOnly && member.kind!=='생산자' && member.kind!=='복수')) return []
    const rank = field==='name' ? Math.min(...memberNames(member).map(name=>nameRank(query,name)))
      : number===query ? 0 : number.startsWith(query) ? 1 : number.includes(query) ? 2 : Infinity
    return Number.isFinite(rank) ? [{member,rank}] : []
  }).sort((a,b)=>a.rank-b.rank).slice(0,5).map(item=>item.member)
}

export function MemberCandidates({members,label,onSelect}: {
  members:Row[]; label:string; onSelect:(member:Row)=>void
}) {
  if (!members.length) return null
  return <ul className="member-candidates" aria-label={label + ' 회원사 후보'}>
    {members.map(member=><li key={member.id}>
      <button type="button" onClick={()=>onSelect(member)}>{String(member.name)} · {String(member.number)}</button>
    </li>)}
  </ul>
}
