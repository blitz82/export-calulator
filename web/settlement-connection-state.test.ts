import {describe,it,expect} from 'vitest'
import {connectionState} from './settlement-connection-state'

describe('connection quantity status',()=>{
  it('completes only when both records are fully consumed',()=>{
    expect(connectionState('600','600.000000',0n,0n)).toBe('complete')
    expect(connectionState('600','600',200_000_000n,0n)).toBe('remaining')
    expect(connectionState('600','600',0n,1n)).toBe('remaining')
  })
  it('requires review for unequal weights and aggregate over-allocation',()=>{
    expect(connectionState('700','690',200_000_000n,0n)).toBe('check')
    expect(connectionState('600','600',-1n,0n)).toBe('check')
    expect(connectionState('600','600',0n,-1n)).toBe('check')
    expect(connectionState('0.000002','0.000001',0n,0n)).toBe('check')
  })
  it('does not show incomplete or malformed inputs as matched',()=>{
    for(const value of ['','-1','invalid','0'])expect(connectionState(value,'1',0n,0n)).toBe('check')
  })
})
