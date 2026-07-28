import { Button } from 'antd'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { identityRoleSummary } from '../presentation/identity'
import { useAuthStore } from '../stores/authStore'
import { UserAvatar } from './UserAvatar'

export function AppShell() {
  const user = useAuthStore((state) => state.user)
  const signOut = useAuthStore((state) => state.signOut)
  const navigate = useNavigate()
  const canApprove = user?.roles.some(
    (role) => role === 'approver' || role === 'admin',
  )
  const canManageKnowledge = user?.roles.some(
    (role) => role === 'operator' || role === 'admin',
  )
  const canManageEvaluations = user?.roles.some(
    (role) => role === 'operator' || role === 'admin',
  )
  const handleLogout = async () => {
    await signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      <aside className="main-sidebar">
        <div className="brand-block">
          <div className="brand-mark">BO</div>
          <div>
            <strong>BizOrch</strong>
            <span>企业智能服务</span>
          </div>
        </div>
        <nav className="main-nav" aria-label="主导航">
          <NavLink className="nav-service" to="/service">智能服务台</NavLink>
          <NavLink className="nav-requests" to="/requests">我的服务请求</NavLink>
          {canApprove && <NavLink className="nav-approvals" to="/approvals">审批工作台</NavLink>}
          {canManageKnowledge && <NavLink className="nav-knowledge" to="/knowledge">企业知识库</NavLink>}
          {canManageEvaluations && <NavLink className="nav-evaluations" to="/evaluations">评测中心</NavLink>}
        </nav>
        <div className="sidebar-foot">
          <NavLink
            className="user-card user-card-link"
            to="/settings"
            aria-label={`打开账号设置，当前账号${user?.username || ''}`}
          >
            <UserAvatar user={user} className="user-avatar" />
            <div>
              <strong>{user?.username || '当前账号'}</strong>
              <small>{identityRoleSummary(user)}</small>
            </div>
          </NavLink>
          <NavLink className="account-settings-link" to="/settings">
            账号设置
          </NavLink>
          <Button type="text" block onClick={() => void handleLogout()}>
            退出登录
          </Button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
