import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import type { RoleName } from '../api/contracts'
import { useAuthStore } from '../stores/authStore'
import { KnowledgeManagerRoute } from './KnowledgeManagerRoute'

function renderRoute(role: RoleName) {
  useAuthStore.setState({
    user: {
      employee_id: `EMP-${role.toUpperCase()}`,
      username: role,
      roles: [role],
    },
    initialized: true,
  })
  return render(
    <MemoryRouter initialEntries={['/knowledge']}>
      <Routes>
        <Route element={<KnowledgeManagerRoute />}>
          <Route path="/knowledge" element={<div>knowledge management</div>} />
        </Route>
        <Route path="/service" element={<div>service desk</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  useAuthStore.setState({ user: null, initialized: true })
})

describe('KnowledgeManagerRoute', () => {
  it.each(['employee', 'approver', 'hr'] satisfies RoleName[])(
    'keeps %s out of knowledge management',
    (role) => {
      renderRoute(role)

      expect(screen.getByText('service desk')).toBeInTheDocument()
      expect(screen.queryByText('knowledge management')).not.toBeInTheDocument()
    },
  )

  it.each(['operator', 'admin'] satisfies RoleName[])(
    'allows %s into knowledge management',
    (role) => {
      renderRoute(role)

      expect(screen.getByText('knowledge management')).toBeInTheDocument()
    },
  )
})
