import {it,expect} from 'vitest'
import {supportPolicy,money} from './support-budget-model'
import type {SupportPlan} from './support-budget-model'
import {defaultPolicy} from './settlement-model'
it('central criteria replace unit/rate fields but keep job-specific restrictions',()=>{
  const p={data:{unit_price:'100',rate:'50',rate_included:false,formula:'실비 한도',rounding:'원 미만 절사',tolerance_kg:'1',tolerance_percent:'',tolerance_rule:'둘 다',reference:'문서 대조',title:'연도 기준'}} as SupportPlan
  const policy=supportPolicy({...defaultPolicy(),unit_price:'999',cap:'3000',deduction:'10'},p)
  expect(policy.unit_price).toBe('100');expect(policy.cap).toBe('3000');expect(policy.deduction).toBe('10');expect(policy.assumptions_confirmed).toBe(true)
})
it('formats budgets without conflating missing, zero and a negative revision delta',()=>{expect(money(null)).toBe('미설정');expect(money('0')).toBe('0');expect(money('-2000')).toBe('-2,000')})
