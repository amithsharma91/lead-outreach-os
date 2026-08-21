import React, { createContext, useContext, useMemo, useState } from 'react'

type AuthContextValue = {
  token: string | null
  isAuthenticated: boolean
  login: (token: string) => Promise<boolean>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null)

  const login = async (candidate: string): Promise<boolean> => {
    const normalized = candidate.trim()

    if (!normalized) {
      return false
    }

    const response = await fetch('/api/leads', {
      headers: {
        Authorization: `Bearer ${normalized}`,
      },
    })

    if (!response.ok) {
      return false
    }

    setToken(normalized)
    return true
  }

  const logout = () => {
    setToken(null)
  }

  const value = useMemo(
    () => ({
      token,
      isAuthenticated: Boolean(token),
      login,
      logout,
    }),
    [token],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider')
  }

  return context
}
