import { beforeEach, describe, expect, it, vi } from 'vitest'

import { listConversations } from './conversations'
import { clearConversationWarmup, loadInitialConversations, warmupConversations } from './conversationWarmup'
import { clearAccessToken, setAccessToken } from './tokenStorage'

vi.mock('./conversations', () => ({ listConversations: vi.fn() }))

describe('conversation warmup', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    clearConversationWarmup()
    clearAccessToken()
  })

  it('shares one pending request with the first chat load', async () => {
    setAccessToken('token-one')
    vi.mocked(listConversations).mockResolvedValue([])
    warmupConversations()
    warmupConversations()

    expect(await loadInitialConversations()).toEqual([])
    expect(listConversations).toHaveBeenCalledTimes(1)
    await loadInitialConversations()
    expect(listConversations).toHaveBeenCalledTimes(2)
  })

  it('never reuses another login token or a failed preload', async () => {
    setAccessToken('token-one')
    vi.mocked(listConversations).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([])
    warmupConversations()
    await Promise.resolve()
    expect(await loadInitialConversations()).toEqual([])

    warmupConversations()
    setAccessToken('token-two')
    expect(await loadInitialConversations()).toEqual([])
    expect(listConversations).toHaveBeenCalledTimes(4)
  })
})
