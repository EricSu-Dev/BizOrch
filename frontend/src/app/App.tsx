import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'

import { ProtectedRoute } from '../components/ProtectedRoute'
import { ApproverRoute } from '../components/ApproverRoute'
import { KnowledgeManagerRoute } from '../components/KnowledgeManagerRoute'
import { EvaluationManagerRoute } from '../components/EvaluationManagerRoute'
import { useAuthStore } from '../stores/authStore'

const AppShell = lazy(() =>
  import('../components/AppShell').then((module) => ({ default: module.AppShell })),
)
const ChatPage = lazy(() =>
  import('../pages/ChatPage').then((module) => ({ default: module.ChatPage })),
)
const LoginPage = lazy(() =>
  import('../pages/LoginPage').then((module) => ({ default: module.LoginPage })),
)
const RequestsPage = lazy(() =>
  import('../pages/RequestsPage').then((module) => ({ default: module.RequestsPage })),
)
const ApprovalsPage = lazy(() =>
  import('../pages/ApprovalsPage').then((module) => ({ default: module.ApprovalsPage })),
)
const WorkflowPage = lazy(() =>
  import('../pages/WorkflowPage').then((module) => ({ default: module.WorkflowPage })),
)
const KnowledgePage = lazy(() =>
  import('../pages/KnowledgePage').then((module) => ({ default: module.KnowledgePage })),
)
const AccountSettingsPage = lazy(() =>
  import('../pages/AccountSettingsPage').then((module) => ({
    default: module.AccountSettingsPage,
  })),
)
const EvaluationPage = lazy(() =>
  import('../pages/EvaluationPage').then((module) => ({ default: module.EvaluationPage })),
)
const HumanReviewsPage = lazy(() =>
  import('../pages/HumanReviewsPage').then((module) => ({ default: module.HumanReviewsPage })),
)

export function App() {
  const initialize = useAuthStore((state) => state.initialize)
  const initialized = useAuthStore((state) => state.initialized)

  useEffect(() => {
    void initialize()
  }, [initialize])

  if (!initialized) {
    return (
      <div className="app-loading" aria-label="正在验证登录状态">
        <Spin size="large" />
      </div>
    )
  }

  return (
    <Suspense fallback={<div className="app-loading"><Spin size="large" /></div>}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<AppShell />}>
              <Route path="/service" element={<ChatPage />} />
              <Route path="/requests" element={<RequestsPage />} />
              <Route path="/requests/:ticketId" element={<RequestsPage />} />
              <Route path="/workflows/:workflowRunId" element={<WorkflowPage />} />
              <Route path="/settings" element={<AccountSettingsPage />} />
              <Route element={<ApproverRoute />}>
                <Route path="/approvals" element={<ApprovalsPage />} />
                <Route path="/approvals/:approvalId" element={<ApprovalsPage />} />
              </Route>
              <Route element={<KnowledgeManagerRoute />}>
                <Route path="/knowledge" element={<KnowledgePage />} />
                <Route path="/knowledge/:documentId" element={<KnowledgePage />} />
              </Route>
              <Route element={<EvaluationManagerRoute />}>
                <Route path="/evaluations" element={<EvaluationPage />} />
                <Route path="/evaluations/:runId" element={<EvaluationPage />} />
                <Route path="/human-reviews" element={<HumanReviewsPage />} />
              </Route>
              <Route path="/" element={<Navigate to="/service" replace />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </Suspense>
  )
}
