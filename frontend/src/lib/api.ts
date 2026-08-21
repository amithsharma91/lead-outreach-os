import React from 'react'
import { useAuth } from '../auth/AuthContext'

export function createApiClient(token: string) {
  const request = async (
    input: string,
    init: RequestInit = {},
  ): Promise<Response> => {
    const headers = new Headers(init.headers)

    headers.set('Authorization', `Bearer ${token}`)

    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    return fetch(input, {
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
