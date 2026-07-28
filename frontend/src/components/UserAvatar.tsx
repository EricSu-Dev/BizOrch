import { useEffect, useState } from 'react'

import { getCurrentAvatar } from '../api/auth'
import type { AuthUser } from '../api/contracts'
import { avatarPresetView } from '../presentation/identity'

interface UserAvatarProps {
  user: AuthUser | null | undefined
  className?: string
  size?: number
  alt?: string
}

export function UserAvatar({
  user,
  className = '',
  size,
  alt = '用户头像',
}: UserAvatarProps) {
  const fallback = avatarPresetView(user)
  const [customSource, setCustomSource] = useState<string>()

  useEffect(() => {
    setCustomSource(undefined)
    if (user?.avatar_url) {
      setCustomSource(user.avatar_url)
      return
    }
    if (!user?.has_custom_avatar || !user.avatar_version) return

    let active = true
    let objectUrl: string | undefined
    void getCurrentAvatar()
      .then((blob) => {
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setCustomSource(objectUrl)
      })
      .catch(() => {
        if (active) setCustomSource(undefined)
      })

    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [user?.avatar_url, user?.avatar_version, user?.has_custom_avatar])

  return (
    <span
      className={`user-avatar-frame ${className}`.trim()}
      style={{
        background: fallback.background,
        ...(size ? { width: size, height: size } : {}),
      }}
    >
      {customSource ? (
        <img src={customSource} alt={alt} />
      ) : (
        <span aria-hidden="true">{fallback.glyph}</span>
      )}
    </span>
  )
}
