import {describe,it,expect} from 'vitest'
import {newReport,selectedMembers} from './monthly-report-model'
import type {ReportCatalog} from './monthly-report-model'
describe('월별 보고 범위',()=>{
  it('월말과 윤년의 기본 대상 기간을 정한다',()=>{expect(newReport('2028-02-17').end).toBe('2028-02-29');expect(newReport('2026-12-01').end).toBe('2026-12-31')})
  it('명칭이 같은 서로 다른 회원사를 합치지 않고 선택 배분만 집계한다',()=>{
    const draft=newReport('2026-07-01');draft.selections=[{snapshot_id:1,portions:[{member_id:1,amount:'300',kg:'20'}],attribution:[],reason:'분할'},{snapshot_id:2,portions:[],attribution:[],reason:''}]
    const catalog:ReportCatalog={files:[],settlements:[{snapshot_id:1,job_id:1,title:'일별',year:2026,start:'',end:'',amount:'1000',members:[{member_id:1,name:'같은명칭',number:'1',amount:'1000',kg:'100'}]},{snapshot_id:2,job_id:2,title:'다른날',year:2026,start:'',end:'',amount:'200',members:[{member_id:2,name:'같은명칭',number:'2',amount:'200',kg:'10'}]}]}
    expect(selectedMembers(draft,catalog).map(m=>[m.member_id,m.amount,m.kg])).toEqual([[1,'300','20'],[2,'200','10']])
  })
})
