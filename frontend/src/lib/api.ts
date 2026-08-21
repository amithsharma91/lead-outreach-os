import React from 'react'
import { useAuth } from '../auth/AuthContext'

/**
 * Resolve the API base URL from the VITE_API_BASE_URL environment variable.
 * In development the Vite dev-server proxy handles /api routing, so the
 * default "/api" works. In production the frontend is a separate static
 * site and must know the backend origin — set VITE_API_BASE_URL to the
 * full backend URL (e.g. "https://your-app.railway.app/api").
 */
export function getApiBaseUrl(): string {
  return (import.meta as any).env?.VITE_API_BASE_URL ?? '/api'
}

export function createApiClient(token: string) {
  const base = getApiBaseUrl()

  const request = async (
    input: string,
    init: RequestInit = {},
  ): Promise<Response> => {
    const url = `${base}${input}`
    const headers = new Headers(init.headers)

    headers.set('Authorization', `Bearer ${token}`)

    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    return fetch(url, {
      ...init,
      headers,
    })
  }

  return {
    get: (input: string) => request(input),

    post: (input: string, body?: unknown) =>
      request(input, {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),

    put: (input: string, body?: unknown) =>
      request(input, {
        method: 'PUT',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),

    patch: (input: string, body?: unknown) =>
      request(input, {
        method: 'PATCH',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),

    delete: (input: string) =>
      request(input, {
        method: 'DELETE',
      }),
  }
}

export function useApiClient() {
  const { token } = useAuth()

  if (!token) {
    throw new Error('Authenticated API client requested without a token')
  }

  return React.useMemo(() => createApiClient(token), [token])
}
