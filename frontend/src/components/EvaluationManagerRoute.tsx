import { Navigate, Outlet } from 'react-router-dom'

import { useAuthStore } from '../stores/authStore'

export function EvaluationManagerRoute() {
  const user = useAuthStore((state) => state.user)
  const allowed = user?.roles.some((role) => role === 'operator' || role === 'admin')

  return allowed ? <Outlet /> : <Navigate to="/service" replace />
}
