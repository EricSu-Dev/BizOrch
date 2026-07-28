import { create } from 'zustand'

import { currentUser, login, logout } from '../api/auth'
import type { AuthUser } from '../api/contracts'
import {
  clearAccessToken,
  getAccessToken,
  setAccessToken,
} from '../api/tokenStorage'

interface AuthState {
  user: AuthUser | null
  initialized: boolean
  submitting: boolean
  initialize: () => Promise<void>
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  setUser: (user: AuthUser) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  initialized: false,
  submitting: false,

  initialize: async () => {
    if (!getAccessToken()) {
      set({ initialized: true, user: null })
      return
    }
    try {
      set({ user: await currentUser(), initialized: true })
    } catch {
      clearAccessToken()
      set({ user: null, initialized: true })
    }
  },

  signIn: async (username, password) => {
    set({ submitting: true })
    try {
      const result = await login(username, password)
      setAccessToken(result.access_token)
      set({ user: result.user })
    } finally {
      set({ submitting: false })
    }
  },

  signOut: async () => {
    try {
      await logout()
    } finally {
      clearAccessToken()
      set({ user: null })
    }
  },

  setUser: (user) => set({ user }),
}))
