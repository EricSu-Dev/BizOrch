import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuthStore } from '../stores/authStore'

export function ProtectedRoute() {
  const user = useAuthStore((state) => state.user)
  const location = useLocation()

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}
