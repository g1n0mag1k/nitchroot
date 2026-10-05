import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearAuth } from '../auth/storage'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  [
    'rounded-lg px-3 py-2 text-sm font-medium transition-colors',
    isActive
      ? 'bg-slate-800 text-white'
      : 'text-slate-400 hover:bg-slate-900 hover:text-white',
  ].join(' ')

export default function Layout() {
  const navigate = useNavigate()

  function handleLogout() {
    clearAuth()
    navigate('/login', { replace: true })
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-6xl flex-col px-4 py-6 sm:px-6">
      <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-400">
            SnitchRoot
          </p>
          <h1 className="text-xl font-semibold text-white">Merchant console</h1>
        </div>
        <nav className="flex flex-wrap gap-1">
          <NavLink to="/dashboard" className={linkClass}>
            Dashboard
          </NavLink>
          <NavLink to="/chain" className={linkClass}>
            Chain
          </NavLink>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-lg px-3 py-2 text-sm font-medium text-slate-400 transition-colors hover:bg-slate-900 hover:text-white"
          >
            Log out
          </button>
        </nav>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  )
}
