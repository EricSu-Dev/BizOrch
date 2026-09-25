import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '../stores/authStore'
import { LoginPage } from './LoginPage'

describe('LoginPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      initialized: true,
      submitting: false,
    })
  })

  afterEach(() => {
    useAuthStore.setState({ user: null, initialized: true })
  })

  it('shows the public demo identities and common password', () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    )

    expect(screen.getByText('统一密码')).toBeInTheDocument()
    expect(screen.getByText('123456')).toBeInTheDocument()
    expect(screen.getByText('普通员工')).toBeInTheDocument()
    expect(screen.getByText('直属审批人')).toBeInTheDocument()
    expect(screen.getByText('知识运营')).toBeInTheDocument()
    expect(screen.getByText('人事经办')).toBeInTheDocument()
    expect(screen.getByText('预算审批人')).toBeInTheDocument()
    expect(screen.getByText('采购审批人')).toBeInTheDocument()
  })

  it('prefills the selected demo account and password', () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    )

    const username = screen.getByLabelText('用户名')
    const password = screen.getByLabelText('密码')
    expect(username).toHaveValue('employee')
    expect(password).toHaveValue('123456')

    fireEvent.click(
      screen.getByRole('button', {
        name: '使用直属审批人账号 manager',
      }),
    )

    expect(username).toHaveValue('manager')
    expect(password).toHaveValue('123456')
  })
})
