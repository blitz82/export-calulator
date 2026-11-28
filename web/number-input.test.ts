import {it,expect} from 'vitest'
import {formatNumeric,normalizeNumeric} from './number-input'
import {intakeErrors} from './sorting-intake-model'
it('preserves precision, zero, empty and trailing decimal digits',()=>{
 for(const [raw,shown] of [['',''],['0','0'],['12345.6700','12,345.6700'],['9007199254740993.123456','9,007,199,254,740,993.123456'],['1234.','1,234.'],['1,234.5','1,234.5'],['invalid','invalid']])expect(formatNumeric(raw)).toBe(shown)
})
it('explains minimal matching fields without asking for OCR or per-field evidence',()=>{
 const d={start:'',end:'',period_type:'일별',producer_id:null,export_quantity:'',evidence:[],change_reason:''}
 expect(Object.keys(intakeErrors(d))).toEqual(['start','producer_id','export_quantity','evidence'])
 expect(intakeErrors({...d,start:'2026-01-01',producer_id:1,export_quantity:'0',evidence:[{file:1,page:1}]})).toEqual({})
})

it('rejects ambiguous separator conventions without silently changing the value',()=>{
 for(const value of ['1,23','1.234,56','1,,234','1e3']){expect(normalizeNumeric(value)).toBeNull();expect(formatNumeric(value)).toBe(value)}
 expect(normalizeNumeric(' 1,234.5600 ')).toBe('1234.5600')
})
