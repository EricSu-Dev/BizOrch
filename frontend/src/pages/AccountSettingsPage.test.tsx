import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  changeCurrentPassword,
  getCurrentAvatar,
  removeCurrentAvatar,
  updateCurrentProfile,
  uploadCurrentAvatar,
} from '../api/auth'
import { useAuthStore } from '../stores/authStore'
import { AccountSettingsPage } from './AccountSettingsPage'

vi.mock('../api/auth', () => ({
  changeCurrentPassword: vi.fn(),
  getCurrentAvatar: vi.fn(),
  removeCurrentAvatar: vi.fn(),
  updateCurrentProfile: vi.fn(),
  uploadCurrentAvatar: vi.fn(),
}))

describe('AccountSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getCurrentAvatar).mockResolvedValue(
      new Blob(['avatar'], { type: 'image/png' }),
    )
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:avatar-preview'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    useAuthStore.setState({
      user: {
        employee_id: 'EMP-KNOWLEDGE-OPERATOR',
        username: 'operator',
        roles: ['operator'],
        avatar_key: 'operations',
      },
      initialized: true,
    })
  })

  afterEach(() => {
    useAuthStore.setState({ user: null, initialized: true })
  })

  it('updates the login name and avatar without changing enterprise identity', async () => {
    vi.mocked(updateCurrentProfile).mockResolvedValue({
      employee_id: 'EMP-KNOWLEDGE-OPERATOR',
      username: 'knowledge.ops',
      roles: ['operator'],
      avatar_key: 'robot',
    })
    render(<AccountSettingsPage />)

    fireEvent.change(screen.getByLabelText('登录账号'), {
      target: { value: 'knowledge.ops' },
    })
    fireEvent.click(screen.getByRole('button', { name: '使用智能助手头像' }))
    fireEvent.click(screen.getByRole('button', { name: '保存账号资料' }))

    await waitFor(() =>
      expect(updateCurrentProfile).toHaveBeenCalledWith({
        username: 'knowledge.ops',
        avatar_key: 'robot',
      }),
    )
    expect(useAuthStore.getState().user?.username).toBe('knowledge.ops')
    expect(screen.getByText('EMP-KNOWLEDGE-OPERATOR')).toBeInTheDocument()
  })

  it('changes the password with the current password and no verification code', async () => {
    vi.mocked(changeCurrentPassword).mockResolvedValue()
    render(<AccountSettingsPage />)

    fireEvent.change(screen.getByLabelText('当前密码'), {
      target: { value: 'current-password' },
    })
    fireEvent.change(screen.getByLabelText('新密码'), {
      target: { value: 'new-secure-password' },
    })
    fireEvent.change(screen.getByLabelText('确认新密码'), {
      target: { value: 'new-secure-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: '修改密码' }))

    await waitFor(() =>
      expect(changeCurrentPassword).toHaveBeenCalledWith({
        current_password: 'current-password',
        new_password: 'new-secure-password',
      }),
    )
    expect(screen.queryByLabelText(/验证码/)).not.toBeInTheDocument()
  })

  it('uploads a supported custom avatar and updates the current identity', async () => {
    vi.mocked(uploadCurrentAvatar).mockResolvedValue({
      employee_id: 'EMP-KNOWLEDGE-OPERATOR',
      username: 'operator',
      roles: ['operator'],
      avatar_key: 'operations',
      has_custom_avatar: true,
      avatar_version: 'avatar-version-1',
    })
    render(<AccountSettingsPage />)
    const file = new File(['small-avatar'], 'avatar.png', {
      type: 'image/png',
    })

    fireEvent.change(screen.getByLabelText('选择头像图片'), {
      target: { files: [file] },
    })

    await waitFor(() =>
      expect(uploadCurrentAvatar).toHaveBeenCalledWith(file),
    )
    expect(useAuthStore.getState().user?.has_custom_avatar).toBe(true)
    expect(await screen.findByRole('img', { name: 'operator的头像' }))
      .toHaveAttribute('src', 'blob:avatar-preview')
  })

  it('removes the uploaded image and falls back to the selected preset', async () => {
    useAuthStore.setState({
      user: {
        employee_id: 'EMP-KNOWLEDGE-OPERATOR',
        username: 'operator',
        roles: ['operator'],
        avatar_key: 'operations',
        has_custom_avatar: true,
        avatar_version: 'avatar-version-1',
      },
    })
    vi.mocked(removeCurrentAvatar).mockResolvedValue({
      employee_id: 'EMP-KNOWLEDGE-OPERATOR',
      username: 'operator',
      roles: ['operator'],
      avatar_key: 'operations',
      has_custom_avatar: false,
      avatar_version: null,
    })
    render(<AccountSettingsPage />)

    fireEvent.click(
      screen.getByRole('button', { name: '恢复内置头像' }),
    )

    await waitFor(() => expect(removeCurrentAvatar).toHaveBeenCalled())
    expect(useAuthStore.getState().user?.has_custom_avatar).toBe(false)
  })
})
