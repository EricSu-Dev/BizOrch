import type { AuthUser, AvatarKey, RoleName } from '../api/contracts'

export const roleLabel: Record<RoleName, string> = {
  employee: '普通员工',
  approver: '审批人',
  operator: '知识运营人员',
  hr: '人事业务经办人',
  admin: '系统管理员',
}

export const avatarPresetOptions: {
  value: AvatarKey
  label: string
  glyph: string
  background: string
}[] = [
  { value: 'person', label: '个人', glyph: '人', background: '#2a5b55' },
  { value: 'operations', label: '运营', glyph: '知', background: '#276a61' },
  { value: 'approval', label: '审批', glyph: '审', background: '#526b3d' },
  { value: 'shield', label: '安全', glyph: '盾', background: '#3f5f73' },
  { value: 'maintenance', label: '维修', glyph: '修', background: '#7a5b3f' },
  { value: 'robot', label: '智能助手', glyph: 'AI', background: '#5d4c78' },
]

const avatarPresetMap = Object.fromEntries(
  avatarPresetOptions.map((item) => [item.value, item]),
) as Record<AvatarKey, (typeof avatarPresetOptions)[number]>

export function defaultAvatarKey(user: AuthUser | null | undefined): AvatarKey {
  if (user?.avatar_key) return user.avatar_key
  if (user?.roles.includes('operator')) return 'operations'
  if (user?.roles.includes('approver')) return 'approval'
  if (user?.roles.includes('admin')) return 'shield'
  return 'person'
}

export function avatarPresetView(user: AuthUser | null | undefined) {
  return avatarPresetMap[defaultAvatarKey(user)]
}

export function identityRoleSummary(
  user: AuthUser | null | undefined,
): string {
  if (!user || user.roles.length === 0) return '已登录账号'
  return user.roles.map((role) => roleLabel[role]).join(' · ')
}
