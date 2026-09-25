import { Button, Form, Input, message as toast } from 'antd'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { publicErrorMessage } from '../api/client'
import { useAuthStore } from '../stores/authStore'

interface LoginValues {
  username: string
  password: string
}

const SHOW_DEMO_ACCOUNTS = import.meta.env.DEV || import.meta.env.VITE_BIZORCH_DEMO_MODE === 'true'
const DEMO_PASSWORD = import.meta.env.VITE_BIZORCH_DEMO_PASSWORD || '123456'

const demoAccounts = [
  { username: 'employee', role: '普通员工', description: '发起企业服务' },
  { username: 'manager', role: '直属审批人', description: '处理业务审批' },
  { username: 'operator', role: '知识运营', description: '管理知识与评测' },
  { username: 'hr', role: '人事经办', description: '办理员工生命周期' },
  { username: 'budget.owner', role: '预算审批人', description: '确认成本中心预算' },
  { username: 'procurement.owner', role: '采购审批人', description: '完成采购终审' },
] as const

export function LoginPage() {
  const user = useAuthStore((state) => state.user)
  const signIn = useAuthStore((state) => state.signIn)
  const submitting = useAuthStore((state) => state.submitting)
  const navigate = useNavigate()
  const location = useLocation()
  const [form] = Form.useForm<LoginValues>()

  if (user) {
    return <Navigate to="/service" replace />
  }

  const submit = async (values: LoginValues) => {
    try {
      await signIn(values.username, values.password)
      const target = (location.state as { from?: string } | null)?.from || '/service'
      navigate(target, { replace: true })
    } catch (error) {
      toast.error(publicErrorMessage(error))
    }
  }

  const selectDemoAccount = (username: string) => {
    form.setFieldsValue({ username, password: DEMO_PASSWORD })
  }

  return (
    <div className="login-page">
      <section className="login-story">
        <div className="login-story-grid" aria-hidden="true" />
        <div className="story-content">
          <div className="story-brand">
            <div className="story-brand-mark">BO</div>
            <div>
              <strong>BizOrch</strong>
              <span>企业智能服务与流程自动化平台</span>
            </div>
          </div>
          <span className="eyebrow">ENTERPRISE AI ORCHESTRATION</span>
          <h1>让企业流程在可控边界内智能运转。</h1>
          <p>
            从一句自然语言请求出发，连接企业知识、业务规则、人工审批与安全执行，
            让每一次业务状态变化都有依据、可恢复、可追踪。
          </p>
          <div className="story-flow" aria-label="企业服务处理流程">
            <span>理解请求</span>
            <i aria-hidden="true" />
            <span>检索制度</span>
            <i aria-hidden="true" />
            <span>人工审批</span>
            <i aria-hidden="true" />
            <span>安全执行</span>
          </div>
          <div className="story-metrics">
            <div><strong>4</strong><span>企业业务场景</span></div>
            <div><strong>3</strong><span>专业智能体角色</span></div>
            <div><strong>全链路</strong><span>关键操作审计</span></div>
          </div>
        </div>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <div className="login-heading">
            <div className="brand-mark large">BO</div>
            <div>
              <span className="eyebrow">WELCOME TO BIZORCH</span>
              <h2>登录企业智能服务台</h2>
            </div>
          </div>
          <p>{SHOW_DEMO_ACCOUNTS ? '选择演示身份，体验不同企业角色的工作台。' : '请使用已分配的企业账号登录。'}</p>
          {SHOW_DEMO_ACCOUNTS && <div className="demo-account-heading">
            <div>
              <strong>可用演示账号</strong>
              <span>点击账号即可自动填入</span>
            </div>
            <span className="demo-password">统一密码&nbsp; <b>{DEMO_PASSWORD}</b></span>
          </div>}
          {SHOW_DEMO_ACCOUNTS && <div className="demo-account-grid">
            {demoAccounts.map((account) => (
              <button
                key={account.username}
                type="button"
                className="demo-account"
                onClick={() => selectDemoAccount(account.username)}
                aria-label={`使用${account.role}账号 ${account.username}`}
              >
                <span className="demo-account-icon" aria-hidden="true">
                  {account.role.slice(0, 1)}
                </span>
                <span>
                  <strong>{account.role}</strong>
                  <code>{account.username}</code>
                  <small>{account.description}</small>
                </span>
              </button>
            ))}
          </div>}
          <Form<LoginValues>
            form={form}
            layout="vertical"
            initialValues={SHOW_DEMO_ACCOUNTS ? { username: 'employee', password: DEMO_PASSWORD } : undefined}
            onFinish={(v) => void submit(v)}
          >
            <Form.Item
              label="用户名"
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input size="large" autoComplete="username" placeholder="employee" />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                size="large"
                autoComplete="current-password"
                placeholder="请输入密码"
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
              登录
            </Button>
          </Form>
          {SHOW_DEMO_ACCOUNTS && <small className="security-note">
            以上账号仅用于作品演示，不对应任何真实企业身份或数据。
          </small>}
        </div>
      </section>
    </div>
  )
}
