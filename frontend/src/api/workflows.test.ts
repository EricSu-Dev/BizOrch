import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowProgress } from './contracts'
import { clearAccessToken, setAccessToken } from './tokenStorage'
import { subscribeWorkflowProgress } from './workflows'

afterEach(() => {
  clearAccessToken()
  vi.unstubAllGlobals()
})

describe('workflow SSE client', () => {
  it('uses a bearer header and parses snapshots split across network chunks', async () => {
    const snapshot: WorkflowProgress = {
      workflow_run_id: 'workflow-run-1',
      ticket_id: 'ticket-1',
      scenario_key: 'access_management',
      state: 'COMPLETED',
      version: 6,
      terminal: true,
      created_at: '2026-07-17T08:00:00Z',
      updated_at: '2026-07-17T08:06:00Z',
      events: [],
      action_plan: null,
    }
    const encoded = new TextEncoder().encode(
      `id: 6\r\nevent: workflow.completed\r\ndata: ${JSON.stringify(snapshot)}\r\n\r\n`,
    )
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, 23))
        controller.enqueue(encoded.slice(23))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    setAccessToken('opaque-test-token')

    const received = await new Promise<WorkflowProgress>((resolve, reject) => {
      subscribeWorkflowProgress('workflow-run-1', resolve, vi.fn(), reject)
    })

    expect(received).toEqual(snapshot)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/workflows/workflow-run-1/stream',
      expect.objectContaining({
        headers: {
          Accept: 'text/event-stream',
          Authorization: 'Bearer opaque-test-token',
        },
      }),
    )
  })
})
