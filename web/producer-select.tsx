import type { Row } from './grid'

export function ProducerSelect({members, onSelect}: {
  members: Row[] | undefined;
  onSelect: (member: Row) => void;
}) {
  const producers = (members ?? []).filter(member => member.kind === '생산자' || member.kind === '복수')
  return <label>생산자 회원사 선택
    <select value="" disabled={!producers.length} onChange={event => {
      const producer = producers.find(member => String(member.id) === event.target.value)
      if (producer) onSelect(producer)
    }}>
      <option value="">{producers.length ? '선택' : '선택 가능한 생산자 회원사 없음'}</option>
      {producers.map(member => <option key={member.id} value={String(member.id)}>
        {String(member.name)} · {String(member.number)}
      </option>)}
    </select>
  </label>
}
