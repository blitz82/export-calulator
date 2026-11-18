export function formatLineNumber(value:string) {
  const text = value.normalize('NFKC').trim()
  return /^[0-9]{1,8}$/.test(text) && Number(text)>0 ? String(Number(text)).padStart(3,'0') : text
}
