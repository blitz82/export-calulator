import {it,expect} from 'vitest'
import {validationMessage} from './validation-message'
it('explains the missing evidence selection in the business context',()=>expect(validationMessage([{loc:['body','payments',0,'evidence',0,'file'],msg:'Input should be greater than or equal to 1'}])).toBe('실제 지급 · 근거 증빙: 원본 PDF를 선택하세요.'))
it('keeps server business messages and limits duplicate field errors',()=>{expect(validationMessage('계약서 등록을 확인하세요.')).toBe('계약서 등록을 확인하세요.');expect(validationMessage([{loc:['body','member_id']},{loc:['body','member_id']}])).toBe('대상 회원사를 선택하세요.')})
