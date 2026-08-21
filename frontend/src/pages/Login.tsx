import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuth()

  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()

    setError('')
    setLoading(true)

    try {
      const valid = await login(token)

      if (!valid) {
        setError('Invalid authentication token.')
        return
      }

      navigate('/dashboard', { replace: true })
    } catch {
      setError('Unable to connect to the backend.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 shadow-xl">
          <div className="mb-8">
            <h1 className="text-2xl font-bold text-white">
              Lead Outreach OS
            </h1>
            <p className="text-sm text-gray-400 mt-2">
              Secure console access
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label
                htmlFor="token"
                className="block text-sm font-medium text-gray-300 mb-2"
              >
                API authentication token
              </label>

              <input
                id="token"
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="Enter your API token"
                className="w-full rounded-lg border border-gray-700 bg-gray-950 px-4 py-3 text-white outline-none focus:border-gray-500"
              />
            </div>

            {error && (
              <div
                role="alert"
                className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300"
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !token.trim()}
              className="w-full rounded-lg bg-white px-4 py-3 text-sm font-semibold text-gray-950 transition hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Authenticating...' : 'Sign in'}
            </button>
          </form>

          <p className="mt-6 text-xs leading-5 text-gray-500">
            Your authentication token is kept in browser memory and is not
            included in the application build.
          </p>
        </div>
      </div>
    </div>
  )
}
