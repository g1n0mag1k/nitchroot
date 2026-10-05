import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { getMerchantId } from '../auth/storage'
import type { ChainResponse } from '../types'

function truncateHash(value: string): string {
  if (!value) return '—'
  return value.length <= 16 ? value : value.slice(0, 16)
}

export default function ChainPage() {
  const merchantId = getMerchantId()
  const [data, setData] = useState<ChainResponse | null>(null)
  const [loading, setLoading] = useState(Boolean(merchantId))
  const [error, setError] = useState<string | null>(
    merchantId ? null : 'Merchant ID missing. Register a merchant first.',
  )

  useEffect(() => {
    if (!merchantId) {
      return
    }

    let cancelled = false
    ;(async () => {
      try {
        const response = await apiFetch<ChainResponse>(
          `/api/v1/merchants/${encodeURIComponent(merchantId)}/chain`,
        )
        if (!cancelled) {
          setData(response)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load chain')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [merchantId])

  if (!merchantId) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-semibold text-white">Evidence chain</h2>
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          Merchant ID missing.{' '}
          <Link to="/register" className="text-emerald-300 underline">
            Register a merchant
          </Link>{' '}
          to view the chain.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-white">Evidence chain</h2>
        <p className="text-sm text-slate-400">
          Hash chain for merchant{' '}
          <span className="font-mono text-slate-300">{merchantId}</span>
        </p>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">Loading chain…</p>
      ) : data ? (
        <>
          <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-5 py-4">
            <p className="text-sm text-slate-400">Verification status</p>
            <p className="mt-1 text-2xl font-semibold text-white">
              {data.verified ? (
                <>
                  <span aria-hidden>✅</span> Verified
                </>
              ) : (
                <>
                  <span aria-hidden>❌</span> Not verified
                </>
              )}
            </p>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-800">
            <table className="min-w-full divide-y divide-slate-800 text-left text-sm">
              <thead className="bg-slate-900/80 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Seq</th>
                  <th className="px-4 py-3 font-medium">Timestamp</th>
                  <th className="px-4 py-3 font-medium">Dossier hash</th>
                  <th className="px-4 py-3 font-medium">Prev hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 bg-slate-950/40">
                {data.entries?.length ? (
                  data.entries.map((entry) => (
                    <tr key={entry.seq} className="hover:bg-slate-900/50">
                      <td className="px-4 py-3 font-mono text-slate-300">{entry.seq}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-300">
                        {entry.timestamp}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-emerald-300">
                        {truncateHash(entry.dossier_hash)}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-400">
                        {truncateHash(entry.prev_hash)}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                      No chain entries yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  )
}
