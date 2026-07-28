import { Navigate, Outlet } from 'react-router-dom'

import { useAuthStore } from '../stores/authStore'

export function ApproverRoute() {
  const user = useAuthStore((state) => state.user)
  const allowed = user?.roles.some((role) => role === 'approver' || role === 'admin')

  return allowed ? <Outlet /> : <Navigate to="/service" replace />
}
