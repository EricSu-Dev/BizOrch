import type { ConversationSummary } from './contracts'
import { listConversations } from './conversations'
import { getAccessToken } from './tokenStorage'

let warmed: {
  token: string
  promise: Promise<ConversationSummary[]>
} | null = null

export function warmupConversations(): void {
  const token = getAccessToken()
  if (!token || warmed?.token === token) return
  const promise = listConversations()
  warmed = { token, promise }
  void promise.catch(() => {
    if (warmed?.promise === promise) warmed = null
  })
}

export function loadInitialConversations(): Promise<ConversationSummary[]> {
  const pending = warmed
  warmed = null
  return pending?.token === getAccessToken()
    ? pending.promise
    : listConversations()
}

export function clearConversationWarmup(): void {
  warmed = null
}
