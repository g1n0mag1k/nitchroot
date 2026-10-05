import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { setApiKey, setMerchantId } from '../auth/storage'
import type { MerchantCreateResponse } from '../types'

export default function RegisterPage() {
  const [name, setName] = useState('')
  const [stripeKey, setStripeKey] = useState('')
  const [probeMinutes, setProbeMinutes] = useState(15)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setCopied(false)

    const trimmedName = name.trim()
    const trimmedStripe = stripeKey.trim()

    if (!trimmedName) {
      setError('Merchant name is required.')
      return
    }
    if (!trimmedStripe.startsWith('rk_')) {
      setError('Stripe restricted key must start with rk_.')
      return
    }

    setSubmitting(true)
    try {
      const response = await apiFetch<MerchantCreateResponse>('/api/v1/merchants', {
        auth: false,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: trimmedName,
          stripe_restricted_key: trimmedStripe,
          probe_interval_seconds: probeMinutes * 60,
        }),
      })

      setCreatedKey(response.api_key)
      setApiKey(response.api_key)
      setMerchantId(response.id)
      setName('')
      setStripeKey('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleCopy() {
    if (!createdKey || copied) return
    try {
      await navigator.clipboard.writeText(createdKey)
      setCopied(true)
    } catch {
      setError('Could not copy to clipboard.')
    }
  }

  return (
    <div className="mx-auto max-w-lg space-y-8">
      <div>
        <h2 className="text-2xl font-semibold text-white">Register merchant</h2>
        <p className="text-sm text-slate-400">
          Create a merchant record and receive a one-time API key.
        </p>
      </div>

      {createdKey ? (
        <div className="space-y-4 rounded-xl border border-amber-900/40 bg-amber-950/20 p-5">
          <p className="text-sm font-medium text-amber-100">
            Save this API key now — it will not be shown again.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <code className="flex-1 break-all rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-emerald-300">
              {createdKey}
            </code>
            <button
              type="button"
              onClick={handleCopy}
              disabled={copied}
              className="shrink-0 rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {copied ? 'Copied' : 'Copy once'}
            </button>
          </div>
        </div>
      ) : null}

      <form
        onSubmit={handleSubmit}
        className="space-y-5 rounded-xl border border-slate-800 bg-slate-900/40 p-5 sm:p-6"
      >
        <label className="block">
          <span className="text-sm font-medium text-slate-300">Merchant name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none ring-emerald-500/40 focus:ring-2"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">Stripe restricted key</span>
          <input
            type="password"
            autoComplete="off"
            value={stripeKey}
            onChange={(e) => setStripeKey(e.target.value)}
            placeholder="rk_..."
            className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none ring-emerald-500/40 focus:ring-2"
          />
        </label>

        <label className="block">
          <span className="flex items-center justify-between text-sm font-medium text-slate-300">
            Probe interval
            <span className="font-mono text-emerald-400">{probeMinutes} min</span>
          </span>
          <input
            type="range"
            min={5}
            max={60}
            step={1}
            value={probeMinutes}
            onChange={(e) => setProbeMinutes(Number(e.target.value))}
            className="mt-3 w-full accent-emerald-500"
          />
          <div className="mt-1 flex justify-between text-xs text-slate-500">
            <span>5 min</span>
            <span>60 min</span>
          </div>
        </label>

        {error ? (
          <p className="text-sm text-rose-400" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create merchant'}
        </button>
      </form>

      <p className="text-center text-sm text-slate-500">
        Already have a key?{' '}
        <Link to="/login" className="text-emerald-400 hover:text-emerald-300">
          Sign in
        </Link>
      </p>
    </div>
  )
}
