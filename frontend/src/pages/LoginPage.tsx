import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { validateApiKey } from '../api/client'
import { isAuthenticated, setApiKey } from '../auth/storage'

export default function LoginPage() {
  const navigate = useNavigate()
  const [apiKey, setApiKeyInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (isAuthenticated()) {
    return <Navigate to="/dashboard" replace />
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const trimmed = apiKey.trim()
    if (!trimmed.startsWith('sr_')) {
      setError('API key must start with sr_')
      return
    }

    setSubmitting(true)
    try {
      const ok = await validateApiKey(trimmed)
      if (!ok) {
        setError('Invalid API key or API unreachable.')
        return
      }
      setApiKey(trimmed)
      navigate('/dashboard', { replace: true })
    } catch {
      setError('Could not reach the API. Check that it is running.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-4 py-12">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 shadow-xl sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-400">
          SnitchRoot
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Sign in</h1>
        <p className="mt-2 text-sm text-slate-400">
          Enter your merchant API key to open the dashboard.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-slate-300">API key</span>
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="sr_..."
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none ring-emerald-500/40 focus:ring-2"
            />
          </label>
          {error ? (
            <p className="text-sm text-rose-400" role="alert">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={submitting || !apiKey.trim()}
            className="w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Checking…' : 'Continue'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          New merchant?{' '}
          <Link to="/register" className="text-emerald-400 hover:text-emerald-300">
            Register
          </Link>
        </p>
      </div>
    </div>
  )
}
