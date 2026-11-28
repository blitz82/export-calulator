import {downloadFilename} from './download-filename'
import {validationMessage} from './validation-message'
const token = fetch('/api/session').then(async response => {
  if (!response.ok) throw new Error('프로그램 연결을 확인하세요.')
  return (await response.json()).token as string
})

export async function request(path: string, init?: RequestInit, handledStatuses: number[] = []) {
  const headers = new Headers(init?.headers)
  headers.set('x-epms-token', await token)
  const response = await fetch('/api/' + path, {...init, headers})
  if (!response.ok && !handledStatuses.includes(response.status)) {
    const error = await response.json().catch(() => ({}))
    throw new Error(validationMessage(error.detail))
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
  const name = downloadFilename(response.headers.get('Content-Disposition'))
  anchor.href = url
  anchor.download = name ?? '공동정산 수출실적 관리'
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
