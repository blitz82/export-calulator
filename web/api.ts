const token = fetch('/api/session').then(async response => {
  if (!response.ok) throw new Error('프로그램 연결을 확인하세요.')
  return (await response.json()).token as string
})

export async function request(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers)
  headers.set('x-epms-token', await token)
  const response = await fetch('/api/' + path, {...init, headers})
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(typeof error.detail === 'string' ? error.detail : '요청을 완료하지 못했습니다.')
  }
  return response
}

export async function get<T>(path: string): Promise<T> {
  return (await request(path)).json()
}

export async function send<T>(path: string, data?: unknown, method = 'POST'): Promise<T> {
  return (await request(path, {method, headers: {'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data)})).json()
}

export async function download(path: string, method = 'GET') {
  const response = await request(path, {method})
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  const name = response.headers.get('Content-Disposition')?.split("UTF-8''")[1]
  anchor.href = url
  anchor.download = name ? decodeURIComponent(name) : '공동정산 수출실적 관리'
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
