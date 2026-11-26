const maximum=9_000_000_000_000_000_000n

export function quantityUnits(value:string):bigint|null {
  if(!/^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?$/.test(value))return null
  const [whole,fraction='']=value.split('.')
  const result=BigInt(whole)*1_000_000n+BigInt(fraction.padEnd(6,'0'))
  return result<=maximum?result:null
}

export function quantityText(value:bigint):string {
  const fraction=(value%1_000_000n).toString().padStart(6,'0').replace(/0+$/,'')
  return (value/1_000_000n).toString()+(fraction?'.'+fraction:'')
}

export function quantityTotal(values:string[]):string|null {
  let total=0n
  for(const value of values){const units=quantityUnits(value);if(units===null)return null;total+=units}
  return quantityText(total)
}
