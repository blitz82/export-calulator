import {describe,it,expect} from 'vitest'
import {boardBalances,candidateInfo,sameProducer,sourceWeightTotal,proposeConnections,compareBatchDates,compareDeclarationDates} from './settlement-board-model'
import type {Batch,Declaration,Edge} from './settlement-model'
const b={id:1,available:'1000',data:{producer:{id:1,name:'동명회사',number:'123-45-67890'},start:'2026-11-02'}} as Batch
const d:Declaration={id:1,revision:1,number:'99099-26-000001',line:'001',kg:'900',maker:'1234567890',maker_name:'동명회사',shipped:'2026-11-04',available:'900',errors:[]}
describe('board candidates and balances',()=>{
  it('keeps chronological card order when connections and remaining weights change',()=>{
    const batches=[{...b,id:3,data:{...b.data,start:'',work_date:'2026-11-01'}},{...b,id:2},{...b,id:1}]
    expect(batches.slice().sort(compareBatchDates).map(r=>r.id)).toEqual([3,1,2])
    const declarations=[{...d,id:2,line:'002'},{...d,id:1,line:'001'},{...d,id:3,shipped:'',declared:'2026-11-03'},{...d,id:4,shipped:'',declared:''}]
    expect(declarations.slice().sort(compareDeclarationDates).map(r=>r.id)).toEqual([3,1,2,4])
    for(const edges of [[],[{batch_id:1,declaration_id:2,left:'500',right:'500'}],[{batch_id:1,declaration_id:1,left:'900',right:'900'}]] as Edge[][]){
      const state=boardBalances(batches,declarations,edges)
      expect(declarations.map(r=>({...r,available:state.right.get(r.id)!.toString()})).slice().sort(compareDeclarationDates).map(r=>r.id)).toEqual([3,1,2,4])
      expect(batches.slice().sort(compareBatchDates).map(r=>r.id)).toEqual([3,1,2])
    }
  })
  it('proposes multiple connections without exceeding a shared pool or validated declarations',()=>{
    const batches=[{...b,id:1,revision:1,ready:true,pool_id:9,pool_available:'1000',available:'600',data:{...b.data,end:'2026-11-02'}},{...b,id:2,revision:1,ready:true,pool_id:9,pool_available:'1000',available:'600',data:{...b.data,end:'2026-11-02'}}]
    const proposals=proposeConnections(batches,[{...d,available:'2000'},{...d,id:2,errors:['계약 미확인']}],[],[1,2],'2026-11-01','2026-11-30')
    expect(proposals.map(e=>e.left)).toEqual(['600','400']);expect(proposals.every(e=>e.declaration_id===1)).toBe(true)
    expect(boardBalances(batches,[{...d,available:'2000'}],proposals).poolRemaining.get(9)).toBe(0n)
  })
  it('matches business identity, never name alone',()=>{expect(sameProducer(b,d)).toBe(true);expect(sameProducer(b,{...d,maker:'',maker_name:'동명회사'})).toBe(false)})
  it('requires identity, period, balance and validation for recommendations',()=>{
    expect(candidateInfo(b,d,900n,'2026-11-01','2026-11-30').recommended).toBe(true)
    for(const item of [{...d,errors:['계약 확인']},{...d,shipped:'2026-12-01'},{...d,maker:'9999999999'}])expect(candidateInfo(b,item,900n,'2026-11-01','2026-11-30').recommended).toBe(false)
    expect(candidateInfo(b,d,0n,'','').recommended).toBe(false)
  })
  it('counts every edge on shared records',()=>{
    const edges=[{batch_id:1,declaration_id:1,left:'600',right:'600'},{batch_id:1,declaration_id:2,left:'400',right:'400'}] as Edge[]
    const result=boardBalances([b],[{...d,available:'600'},{...d,id:2,available:'500'}],edges)
    expect(result.left.get(1)).toBe(0n);expect(result.right.get(2)).toBe(100_000_000n);expect(result.states).toEqual(['complete','remaining'])
  })
  it('shares a monthly pool between daily details without counting it twice',()=>{
    const bs=[{...b,id:1,pool_id:9,pool_available:'1000',available:'600'},{...b,id:2,pool_id:9,pool_available:'1000',available:'600'}]
    const balance=boardBalances(bs,[d],[{batch_id:1,declaration_id:1,left:'600',right:'600'}] as Edge[])
    expect(sourceWeightTotal(bs)).toBe(1_000_000_000n);expect(balance.left.get(2)).toBe(400_000_000n);expect(sourceWeightTotal(bs,balance)).toBe(400_000_000n)
  })
})
