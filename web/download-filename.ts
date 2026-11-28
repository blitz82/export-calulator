export function downloadFilename(header:string|null):string|undefined{
  if(!header)return undefined
  const encoded=header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i),plain=header.match(/filename\s*=\s*(?:"([^"]*)"|([^;]+))/i)
  let name:string|undefined
  try{name=encoded?decodeURIComponent(encoded[1].trim()):plain?.[1]??plain?.[2]?.trim()}catch{name=plain?.[1]??plain?.[2]?.trim()}
  return name?.split(/[\\/]/).pop()?.replace(/[\x00-\x1f]/g,'')||undefined
}
