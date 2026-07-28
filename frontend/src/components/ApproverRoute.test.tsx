import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import type { RoleName } from '../api/contracts'
import { useAuthStore } from '../stores/authStore'
import { ApproverRoute } from './ApproverRoute'

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={['/approvals']}>
      <Routes>
        <Route element={<ApproverRoute />}>
          <Route path="/approvals" element={<div>approval workbench</div>} />
        </Route>
        <Route path="/service" element={<div>service desk</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  useAuthStore.setState({ user: null, initialized: true })
})

describe('ApproverRoute', () => {
  it.each(['employee', 'operator', 'hr'] satisfies RoleName[])(
    'keeps %s out of the approver user experience',
    (role) => {
    useAuthStore.setState({
      user: {
        employee_id: `EMP-${role.toUpperCase()}`,
        username: role,
        roles: [role],
      },
      initialized: true,
    })

    renderRoute()

    expect(screen.getByText('service desk')).toBeInTheDocument()
    expect(screen.queryByText('approval workbench')).not.toBeInTheDocument()
    },
  )

  it('allows an assigned approver into the workbench route', () => {
    useAuthStore.setState({
      user: { employee_id: 'EMP-MANAGER', username: 'manager', roles: ['approver'] },
      initialized: true,
    })

    renderRoute()

    expect(screen.getByText('approval workbench')).toBeInTheDocument()
  })
})
