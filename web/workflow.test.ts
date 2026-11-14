import { describe, expect, it } from 'vitest'
import { parsePaste } from './paste'

describe('원장 붙여넣기', () => {
  it('빈 셀 및 식별자 선행 0을 유지한다', () => {
    const result = parsePaste('0806100000\t\t1\r\n0806200000\t둘째\t2', ['hs','product','line'])
    expect(result.map(row=>row.values)).toEqual([
      {hs:'0806100000',product:'',line:'1'}, {hs:'0806200000',product:'둘째',line:'2'},
    ])
  })
  it('범위를 초과하는 열의 유실을 막는다', () => {
    expect(()=>parsePaste('하나\t둘',['name'])).toThrow('열')
  })
})
