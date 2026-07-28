import { useEffect, useRef, useState } from 'react'
import { Button, Input, message as toast } from 'antd'

import {
  changeCurrentPassword,
  removeCurrentAvatar,
  updateCurrentProfile,
  uploadCurrentAvatar,
} from '../api/auth'
import { publicErrorMessage } from '../api/client'
import type { AvatarKey } from '../api/contracts'
import {
  avatarPresetOptions,
  defaultAvatarKey,
  identityRoleSummary,
} from '../presentation/identity'
import { useAuthStore } from '../stores/authStore'
import { UserAvatar } from '../components/UserAvatar'

const usernamePattern = /^[A-Za-z0-9._-]{3,100}$/
const allowedAvatarTypes = new Set(['image/png', 'image/jpeg', 'image/webp'])
const maxAvatarBytes = 1024 * 1024

export function AccountSettingsPage() {
  const user = useAuthStore((state) => state.user)
  const setUser = useAuthStore((state) => state.setUser)
  const [username, setUsername] = useState(user?.username || '')
  const [avatarKey, setAvatarKey] = useState<AvatarKey>(
    defaultAvatarKey(user),
  )
  const [savingProfile, setSavingProfile] = useState(false)
  const [savingAvatar, setSavingAvatar] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [savingPassword, setSavingPassword] = useState(false)
  const avatarInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setUsername(user?.username || '')
    setAvatarKey(defaultAvatarKey(user))
  }, [user])

  const profileChanged =
    username.trim().toLowerCase() !== user?.username ||
    avatarKey !== defaultAvatarKey(user)
  const usernameValid = usernamePattern.test(username.trim())

  const saveProfile = async () => {
    if (!usernameValid || savingProfile) return
    setSavingProfile(true)
    try {
      const updated = await updateCurrentProfile({
        username: username.trim(),
        avatar_key: avatarKey,
      })
      setUser(updated)
      toast.success('账号资料已更新')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSavingProfile(false)
    }
  }

  const uploadAvatar = async (file: File | undefined) => {
    if (!file || savingAvatar) return
    if (!allowedAvatarTypes.has(file.type)) {
      toast.warning('头像仅支持 PNG、JPEG 和 WebP 图片')
      return
    }
    if (file.size > maxAvatarBytes) {
      toast.warning('头像图片不能超过 1MB')
      return
    }
    setSavingAvatar(true)
    try {
      setUser(await uploadCurrentAvatar(file))
      toast.success('头像已更新')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSavingAvatar(false)
      if (avatarInput.current) avatarInput.current.value = ''
    }
  }

  const clearUploadedAvatar = async () => {
    if (savingAvatar) return
    setSavingAvatar(true)
    try {
      setUser(await removeCurrentAvatar())
      toast.success('已恢复为内置头像')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSavingAvatar(false)
    }
  }

  const savePassword = async () => {
    if (
      savingPassword ||
      currentPassword.length < 8 ||
      newPassword.length < 8
    ) {
      return
    }
    if (newPassword !== confirmPassword) {
      toast.warning('两次输入的新密码不一致')
      return
    }
    setSavingPassword(true)
    try {
      await changeCurrentPassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      toast.success('密码已修改，其他登录会话已退出')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSavingPassword(false)
    }
  }

  return (
    <div className="account-settings-page">
      <header className="account-settings-header">
        <span className="eyebrow">ACCOUNT SETTINGS</span>
        <h1>账号设置</h1>
        <p>维护登录账号、头像和密码。企业身份编号与角色由组织统一管理。</p>
      </header>

      <div className="account-settings-grid">
        <section className="settings-card">
          <div className="settings-card-heading">
            <div>
              <span className="eyebrow">PROFILE</span>
              <h2>账号资料</h2>
            </div>
            <span>{identityRoleSummary(user)}</span>
          </div>

          <div className="custom-avatar-setting">
            <UserAvatar
              user={user}
              className="profile-avatar-preview"
              size={72}
              alt={`${user?.username || '当前账号'}的头像`}
            />
            <div>
              <strong>自定义头像</strong>
              <p>支持 PNG、JPEG、WebP，文件大小不超过 1MB。</p>
              <div className="custom-avatar-actions">
                <Button
                  loading={savingAvatar}
                  onClick={() => avatarInput.current?.click()}
                >
                  {user?.has_custom_avatar ? '更换图片' : '上传图片'}
                </Button>
                {user?.has_custom_avatar && (
                  <Button
                    disabled={savingAvatar}
                    onClick={() => void clearUploadedAvatar()}
                  >
                    恢复内置头像
                  </Button>
                )}
              </div>
              <input
                ref={avatarInput}
                className="visually-hidden"
                aria-label="选择头像图片"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) =>
                  void uploadAvatar(event.target.files?.[0])
                }
              />
            </div>
          </div>

          <div className="avatar-setting">
            <strong>内置头像</strong>
            <small>没有上传自定义图片时，将显示所选内置头像。</small>
            <div className="avatar-options" aria-label="选择头像">
              {avatarPresetOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={avatarKey === option.value ? 'selected' : ''}
                  aria-pressed={avatarKey === option.value}
                  aria-label={`使用${option.label}头像`}
                  onClick={() => setAvatarKey(option.value)}
                >
                  <span style={{ background: option.background }}>
                    {option.glyph}
                  </span>
                  <small>{option.label}</small>
                </button>
              ))}
            </div>
          </div>

          <label className="settings-field">
            <span>登录账号</span>
            <Input
              aria-label="登录账号"
              value={username}
              maxLength={100}
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
            />
            <small>
              3–100位，仅支持英文字母、数字、点、下划线和短横线；修改后请使用新账号登录。
            </small>
          </label>

          <div className="settings-readonly">
            <div>
              <span>企业身份编号</span>
              <strong>{user?.employee_id || '—'}</strong>
            </div>
            <p>该编号用于业务归属和审计，不允许个人修改。</p>
          </div>

          <Button
            type="primary"
            loading={savingProfile}
            disabled={!profileChanged || !usernameValid}
            onClick={() => void saveProfile()}
          >
            保存账号资料
          </Button>
        </section>

        <section className="settings-card">
          <div className="settings-card-heading">
            <div>
              <span className="eyebrow">SECURITY</span>
              <h2>修改密码</h2>
            </div>
          </div>

          <div className="password-notice">
            无需短信或邮箱验证码。为了防止他人利用未锁定的电脑改密，需要验证当前密码。
          </div>

          <label className="settings-field">
            <span>当前密码</span>
            <Input.Password
              aria-label="当前密码"
              value={currentPassword}
              autoComplete="current-password"
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </label>
          <label className="settings-field">
            <span>新密码</span>
            <Input.Password
              aria-label="新密码"
              value={newPassword}
              autoComplete="new-password"
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <small>至少8位，不能与当前密码相同。</small>
          </label>
          <label className="settings-field">
            <span>确认新密码</span>
            <Input.Password
              aria-label="确认新密码"
              value={confirmPassword}
              autoComplete="new-password"
              status={
                confirmPassword && confirmPassword !== newPassword
                  ? 'error'
                  : undefined
              }
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </label>

          <Button
            type="primary"
            loading={savingPassword}
            disabled={
              currentPassword.length < 8 ||
              newPassword.length < 8 ||
              confirmPassword.length < 8
            }
            onClick={() => void savePassword()}
          >
            修改密码
          </Button>
        </section>
      </div>
    </div>
  )
}
