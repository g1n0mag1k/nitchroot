import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { getMerchantId } from '../auth/storage'
import type { Discrepancy } from '../types'

const REFRESH_MS = 60_000

function severityBadge(severity: string) {
  const upper = severity.toUpperCase()
  if (upper === 'HIGH') {
    return { icon: '🔴', className: 'text-rose-400' }
  }
  return { icon: '🟡', className: 'text-amber-400' }
}

function normalizeDiscrepancies(payload: unknown): Discrepancy[] {
  if (Array.isArray(payload)) {
    return payload as Discrepancy[]
  }
  if (payload && typeof payload === 'object' && 'items' in payload) {
    const items = (payload as { items?: unknown }).items
    return Array.isArray(items) ? (items as Discrepancy[]) : []
  }
  return []
}

export default function DashboardPage() {
  const merchantId = getMerchantId()
  const [rows, setRows] = useState<Discrepancy[]>([])
  const [loading, setLoading] = useState(Boolean(merchantId))
  const [error, setError] = useState<string | null>(
    merchantId ? null : 'Merchant ID missing. Register a merchant to populate it.',
  )

  useEffect(() => {
    if (!merchantId) {
      return
    }

    let cancelled = false

    const run = async () => {
      try {
        const data = await apiFetch<unknown>(
          `/api/v1/discrepancies?merchant_id=${encodeURIComponent(merchantId)}`,
        )
        if (cancelled) return
        setRows(normalizeDiscrepancies(data))
        setError(null)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load discrepancies')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void run()
    const timer = window.setInterval(() => {
      void run()
    }, REFRESH_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [merchantId])

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-white">Open issues</h2>
          <p className="text-sm text-slate-400">
            Reconciliation discrepancies for merchant{' '}
            <span className="font-mono text-slate-300">{merchantId ?? '—'}</span>
          </p>
        </div>
        <p className="text-xs text-slate-500">Auto-refreshes every 60s</p>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      ) : null}

      {loading ? (
        <p className="text-sm text-slate-400">Loading discrepancies…</p>
      ) : rows.length === 0 && !error ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-6 py-10 text-center">
          <p className="text-lg font-medium text-slate-200">No open issues</p>
          <p className="mt-1 text-sm text-slate-500">Probes and webhooks look aligned.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="min-w-full divide-y divide-slate-800 text-left text-sm">
            <thead className="bg-slate-900/80 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Detected</th>
                <th className="px-4 py-3 font-medium">Severity</th>
                <th className="px-4 py-3 font-medium">Description</th>
                <th className="px-4 py-3 font-medium">Dossier</th>
                <th className="px-4 py-3 font-medium">Resolved</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-950/40">
              {rows.map((row, index) => {
                const badge = severityBadge(row.severity)
                return (
                  <tr key={`${row.detected_at}-${index}`} className="hover:bg-slate-900/50">
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-300">
                      {row.detected_at}
                    </td>
                    <td className={`px-4 py-3 font-medium ${badge.className}`}>
                      <span aria-hidden>{badge.icon}</span>{' '}
                      <span>{row.severity}</span>
                    </td>
                    <td className="max-w-xs px-4 py-3 text-slate-200">{row.description}</td>
                    <td className="px-4 py-3">
                      {row.dossier_path ? (
                        <a
                          href={row.dossier_path}
                          className="text-emerald-400 hover:text-emerald-300"
                          target="_blank"
                          rel="noreferrer"
                        >
                          View
                        </a>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-300">
                      {row.resolved ? 'Yes' : 'No'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
