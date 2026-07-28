import { apiClient } from './client'
import type {
  ConversationMessage,
  ConversationSummary,
  ConversationTurnResult,
} from './contracts'

export async function listConversations(): Promise<ConversationSummary[]> {
  const { data } = await apiClient.get<ConversationSummary[]>('/conversations')
  return data
}

export async function listMessages(
  conversationId: string,
): Promise<ConversationMessage[]> {
  const { data } = await apiClient.get<ConversationMessage[]>(
    `/conversations/${conversationId}/messages`,
  )
  return data
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const { data } = await apiClient.patch<ConversationSummary>(
    `/conversations/${conversationId}`,
    { title },
  )
  return data
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await apiClient.delete(`/conversations/${conversationId}`)
}

export async function sendAgentMessage(input: {
  message: string
  clientMessageId: string
  conversationId?: string
}): Promise<ConversationTurnResult> {
  const { data } = await apiClient.post<ConversationTurnResult>(
    '/agent/messages',
    {
      message: input.message,
      client_message_id: input.clientMessageId,
      conversation_id: input.conversationId,
    },
    { timeout: 90_000 },
  )
  return data
}
