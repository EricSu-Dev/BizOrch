import { Button, Form, Input, message as toast } from 'antd'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { publicErrorMessage } from '../api/client'
import { useAuthStore } from '../stores/authStore'

interface LoginValues {
  username: string
  password: string
}

export function LoginPage() {
  const user = useAuthStore((state) => state.user)
  const signIn = useAuthStore((state) => state.signIn)
  const submitting = useAuthStore((state) => state.submitting)
  const navigate = useNavigate()
  const location = useLocation()

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

  return (
    <div className="login-page">
      <section className="login-story">
        <div className="story-content">
          <span className="eyebrow">ENTERPRISE ORCHESTRATION</span>
          <h1>让企业服务从一句话开始，按规则可靠完成。</h1>
          <p>
            BizOrch 将知识检索、流程编排、人工审批和受控执行连接成一条可追踪的业务闭环。
          </p>
          <div className="story-points">
            <span>可审批</span><span>可恢复</span><span>可追踪</span>
          </div>
        </div>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <div className="brand-mark large">BO</div>
          <span className="eyebrow">BIZORCH</span>
          <h2>登录企业智能服务台</h2>
          <p>使用演示账号进入权限申请与企业知识服务。</p>
          <Form<LoginValues> layout="vertical" onFinish={(v) => void submit(v)}>
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
          <small className="security-note">登录令牌不会显示在页面或写入日志。</small>
        </div>
      </section>
    </div>
  )
}
