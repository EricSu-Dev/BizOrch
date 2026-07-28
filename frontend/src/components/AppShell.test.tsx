import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import type { RoleName } from '../api/contracts'
import { useAuthStore } from '../stores/authStore'
import { AppShell } from './AppShell'

function renderShell(role: RoleName) {
  useAuthStore.setState({
    user: {
      employee_id:
        role === 'operator'
          ? 'EMP-KNOWLEDGE-OPERATOR'
          : `EMP-${role.toUpperCase()}`,
      username: role,
      roles: [role],
    },
    initialized: true,
  })
  render(
    <MemoryRouter initialEntries={['/service']}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/service" element={<div>service page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  useAuthStore.setState({ user: null, initialized: true })
})

describe('AppShell role navigation', () => {
  it('keeps the login name and merges the role description', () => {
    renderShell('operator')

    expect(screen.getByText('operator')).toBeInTheDocument()
    expect(screen.getByText('知识运营人员')).toBeInTheDocument()
    expect(screen.queryByText('EMP-KNOWLEDGE-OPERATOR')).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: '账号设置' }),
    ).toHaveAttribute('href', '/settings')
  })

  it.each(['operator', 'admin'] satisfies RoleName[])(
    'shows enterprise knowledge navigation to %s',
    (role) => {
      renderShell(role)

      expect(
        screen.getByRole('link', { name: '企业知识库' }),
      ).toHaveAttribute('href', '/knowledge')
    },
  )

  it.each(['employee', 'approver', 'hr'] satisfies RoleName[])(
    'hides enterprise knowledge navigation from %s',
    (role) => {
      renderShell(role)

      expect(
        screen.queryByRole('link', { name: '企业知识库' }),
      ).not.toBeInTheDocument()
    },
  )
})
