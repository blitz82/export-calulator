export function parsePaste(text: string, keys: string[]) {
  const rows = text.split(/\r?\n/).filter(line => line.trim())
  if (!rows.length || rows.length > 1000) throw new Error('한 번에 1,000행 이하를 붙여넣으세요.')
  return rows.map(line => {
    const cells = line.split('\t')
    if (cells.length > keys.length) throw new Error('표준 양식의 열 순서와 개수를 확인하세요.')
    return {values:Object.fromEntries(cells.map((value,index) => [keys[index],value])), reason:'Excel 붙여넣기'}
  })
}
