import { getApiKey } from '../auth/storage'

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function authHeaders(includeAuth: boolean): HeadersInit {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }
  if (includeAuth) {
    const key = getApiKey()
    if (key) {
      headers.Authorization = `Bearer ${key}`
    }
  }
  return headers
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  const { auth = true, headers: extraHeaders, ...init } = options
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      ...authHeaders(auth),
      ...(extraHeaders ?? {}),
    },
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string; error?: string }
      detail = body.detail ?? body.error ?? detail
    } catch {
      /* ignore non-json bodies */
    }
    throw new ApiError(detail || `Request failed (${response.status})`, response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export async function validateApiKey(apiKey: string): Promise<boolean> {
  const response = await fetch(`${baseUrl}/health`, {
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
  })
  return response.ok
}
