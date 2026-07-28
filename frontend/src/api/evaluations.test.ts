import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from './client'
import {
  createEvaluationRun,
  createEvaluationRetest,
  listEvaluationCases,
  updateEvaluationBadCase,
} from './evaluations'

vi.mock('./client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const mockedGet = vi.mocked(apiClient.get)
const mockedPost = vi.mocked(apiClient.post)
const mockedPatch = vi.mocked(apiClient.patch)

describe('evaluation API client', () => {
  beforeEach(() => vi.clearAllMocks())

  it('creates a fixed-suite run with an idempotency key and explicit live confirmation', async () => {
    mockedPost.mockResolvedValue({ data: { run_id: 'run-1' } })
    await createEvaluationRun({
      suite_key: 'v5_procurement', mode: 'LIVE_READ_ONLY',
      case_ids: ['live-001'], confirm_live_external_calls: true,
    }, 'client-command-1')

    expect(mockedPost).toHaveBeenCalledWith('/evaluations/runs', {
      suite_key: 'v5_procurement', mode: 'LIVE_READ_ONLY',
      case_ids: ['live-001'], confirm_live_external_calls: true,
    }, { headers: { 'Idempotency-Key': 'client-command-1' } })
  })

  it('gets only paged, filtered result projections rather than suite source assets', async () => {
    mockedGet.mockResolvedValue({ data: { items: [], page: 1, page_size: 100, total: 0 } })
    await listEvaluationCases('run-1', { status: 'FAILED', category: 'SAFETY_SCHEMA' })

    expect(mockedGet).toHaveBeenCalledWith('/evaluations/runs/run-1/cases', {
      params: { page: 1, page_size: 100, status: 'FAILED', category: 'SAFETY_SCHEMA' },
    })
  })

  it('passes the server-issued optimistic version to Bad Case commands and retests', async () => {
    mockedPatch.mockResolvedValue({ data: { bad_case_id: 'bad-1' } })
    mockedPost.mockResolvedValue({ data: { run_id: 'retest-1' } })
    await updateEvaluationBadCase('bad-1', {
      action: 'START_WORK', expected_version: 4, assignee_id: 'operator',
    })
    await createEvaluationRetest('bad-1', 5, 'retest-command-1')

    expect(mockedPatch).toHaveBeenCalledWith('/evaluations/bad-cases/bad-1', {
      action: 'START_WORK', expected_version: 4, assignee_id: 'operator',
    })
    expect(mockedPost).toHaveBeenCalledWith('/evaluations/bad-cases/bad-1/retest', {
      expected_version: 5,
    }, { headers: { 'Idempotency-Key': 'retest-command-1' } })
  })
})
