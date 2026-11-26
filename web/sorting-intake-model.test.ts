import {it,expect} from 'vitest'
import {ocrInput,ocrMoneyInput,belongs} from './sorting-intake-model'
it('applies only unambiguous OCR numbers and preserves unknown versus zero',()=>{
  expect(ocrInput('export_quantity','1,200 kg','kg')).toBe('1200');expect(ocrInput('export_quantity','1.2톤','kg')).toBe('1200')
  expect(ocrInput('total','1200kg','t')).toBe('1.2');expect(ocrInput('domestic','0','kg')).toBe('0')
  for(const value of ['','약 1,200kg','1,200kg 내수 20kg','-1','12,34'])expect(ocrInput('export_quantity',value,'kg')).toBeNull()
})
it('does not invent dates or map payroll figures into sorting weights',()=>{
  expect(ocrInput('start','2026년 11월 2일','kg')).toBe('2026-11-02');expect(ocrInput('start','2026-02-30','kg')).toBeNull()
  expect(ocrInput('start','11월 2일','kg')).toBeNull();expect(ocrInput('gross','1000','kg')).toBeNull()
})
it('keeps corrected source IDs visible in their previous reception groups',()=>{expect(belongs({receipt_id:2,receipt_ids:[1,2]},1)).toBe(true);expect(belongs({receipt_id:2},1)).toBe(false)})
it('applies only explicit won payroll figures',()=>{expect(ocrMoneyInput('900,000원')).toBe('900000');expect(ocrMoneyInput('약 90만원')).toBeNull();expect(ocrMoneyInput('100 USD')).toBeNull()})
