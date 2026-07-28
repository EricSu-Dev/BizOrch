import { apiBaseUrl, apiClient } from './client'
import type { WorkflowProgress } from './contracts'
import { getAccessToken } from './tokenStorage'

export async function getWorkflowProgress(
  workflowRunId: string,
): Promise<WorkflowProgress> {
  const { data } = await apiClient.get<WorkflowProgress>(
    `/workflows/${encodeURIComponent(workflowRunId)}`,
  )
  return data
}

function parseSseBlock(block: string): WorkflowProgress | undefined {
  let eventName = ''
  const dataLines: string[] = []
  for (const rawLine of block.split(/\r?\n/)) {
    if (rawLine.startsWith('event:')) {
      eventName = rawLine.slice(6).trim()
    } else if (rawLine.startsWith('data:')) {
      dataLines.push(rawLine.slice(5).trimStart())
    }
  }
  if (!eventName.startsWith('workflow.') || dataLines.length === 0) {
    return undefined
  }
  return JSON.parse(dataLines.join('\n')) as WorkflowProgress
}

async function consumeSse(
  response: Response,
  onSnapshot: (snapshot: WorkflowProgress) => void,
): Promise<void> {
  if (!response.body) throw new Error('实时进度响应不可读取')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const snapshot = parseSseBlock(block)
      if (snapshot) onSnapshot(snapshot)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) return
  }
}

export function subscribeWorkflowProgress(
  workflowRunId: string,
  onSnapshot: (snapshot: WorkflowProgress) => void,
  onOpen: () => void,
  onError: (error: unknown) => void,
): () => void {
  const controller = new AbortController()
  const connect = async () => {
    try {
      const token = getAccessToken()
      const response = await fetch(
        `${apiBaseUrl}/workflows/${encodeURIComponent(workflowRunId)}/stream`,
        {
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        },
      )
      if (!response.ok) {
        throw new Error(`实时进度连接失败（${response.status}）`)
      }
      onOpen()
      await consumeSse(response, onSnapshot)
    } catch (error) {
      if (!controller.signal.aborted) onError(error)
    }
  }
  void connect()
  return () => controller.abort()
}
