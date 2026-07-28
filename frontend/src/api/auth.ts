import { apiClient } from './client'
import type { AuthUser, AvatarKey, LoginResult } from './contracts'

export async function login(username: string, password: string): Promise<LoginResult> {
  const { data } = await apiClient.post<LoginResult>('/auth/login', {
    username,
    password,
  })
  return data
}

export async function currentUser(): Promise<AuthUser> {
  const { data } = await apiClient.get<AuthUser>('/auth/me')
  return data
}

export async function updateCurrentProfile(input: {
  username: string
  avatar_key: AvatarKey
}): Promise<AuthUser> {
  const { data } = await apiClient.patch<AuthUser>('/auth/me', input)
  return data
}

export async function uploadCurrentAvatar(file: File): Promise<AuthUser> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await apiClient.post<AuthUser>('/auth/me/avatar', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function getCurrentAvatar(): Promise<Blob> {
  const { data } = await apiClient.get<Blob>('/auth/me/avatar', {
    responseType: 'blob',
  })
  return data
}

export async function removeCurrentAvatar(): Promise<AuthUser> {
  const { data } = await apiClient.delete<AuthUser>('/auth/me/avatar')
  return data
}

export async function changeCurrentPassword(input: {
  current_password: string
  new_password: string
}): Promise<void> {
  await apiClient.post('/auth/me/password', input)
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}
