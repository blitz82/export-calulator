import {describe,it,expect} from 'vitest'
import {quantityUnits,quantityText,quantityTotal} from './sorting-quantity'

describe('신청 목록의 정확한 kg 표시',()=>{
  it('부동소수점 오차 없이 소수점 6자리와 큰 값을 합산한다',()=>{
    expect(quantityTotal(['0.1','0.2','0.000001'])).toBe('0.300001')
    expect(quantityTotal(['9000000000000','0.000001'])).toBe('9000000000000.000001')
    expect(quantityText(1n)).toBe('0.000001')
  })
  it.each(['','-1','NaN','Infinity','1e3','0.0000001','9000000000000.000001','01'])(
    '잘못된 수량 %s는 합산 가능한 값으로 바꾸지 않는다',value=>{
      expect(quantityUnits(value)).toBeNull()
      expect(quantityTotal(['100',value])).toBeNull()
    })
})
