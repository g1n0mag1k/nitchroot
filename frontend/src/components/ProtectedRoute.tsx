import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { isAuthenticated } from '../auth/storage'

export default function ProtectedRoute() {
  const location = useLocation()

  if (!isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
