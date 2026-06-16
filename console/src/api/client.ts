// ============================================================
// API client — fetch wrapper with auth header
// ============================================================

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8468'
const X_ROLE = (import.meta.env.VITE_X_ROLE as string | undefined) ?? 'FRAUD_ANALYST'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Role': X_ROLE,
      ...(options?.headers ?? {}),
    },
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      // ignore parse error
    }
    throw new Error(detail)
  }

  return res.json() as Promise<T>
}

export async function get<T>(path: string): Promise<T> {
  return apiFetch<T>(path)
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
