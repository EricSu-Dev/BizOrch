import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as evaluationApi from '../api/evaluations'
import { EvaluationPage } from './EvaluationPage'

vi.mock('../api/evaluations', () => ({
  archiveEvaluationBadCase: vi.fn(), closeEvaluationBadCase: vi.fn(),
  createEvaluationRetest: vi.fn(), createEvaluationRun: vi.fn(),
  getEvaluationComparison: vi.fn(), getEvaluationRun: vi.fn(),
  listEvaluationBadCases: vi.fn(), listEvaluationBaselines: vi.fn(),
  listEvaluationCases: vi.fn(), listEvaluationRuns: vi.fn(), listEvaluationSuites: vi.fn(),
  setEvaluationBaseline: vi.fn(), updateEvaluationBadCase: vi.fn(), verifyEvaluationBadCase: vi.fn(),
}))

function renderPage(entry = '/evaluations') {
  return render(<MemoryRouter initialEntries={[entry]}><Routes><Route path="/evaluations" element={<EvaluationPage />} /><Route path="/evaluations/:runId" element={<EvaluationPage />} /></Routes></MemoryRouter>)
}

describe('EvaluationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(evaluationApi.listEvaluationSuites).mockResolvedValue({ items: [{ suite_key: 'v5_procurement', suite_name: '采购安全套件', suite_version: '2026.1', content_digest: 'sha256:abc', case_count: 9, categories: ['SAFETY_SCHEMA'], supported_modes: ['CONTRACT_ONLY', 'LIVE_READ_ONLY'] }], page: 1, page_size: 20, total: 1 })
    vi.mocked(evaluationApi.listEvaluationRuns).mockResolvedValue({ items: [], page: 1, page_size: 30, total: 0 })
    vi.mocked(evaluationApi.listEvaluationBadCases).mockResolvedValue({ items: [], page: 1, page_size: 50, total: 0 })
    vi.mocked(evaluationApi.listEvaluationBaselines).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
  })

  it('shows a safe default mode and requires explicit confirmation before live read-only execution', async () => {
    renderPage()
    await screen.findByText('最近评测运行')
    fireEvent.click(screen.getByRole('button', { name: '新建评测运行' }))

    expect(screen.getByText('离线合约校验（默认，不调用外部模型）')).toBeInTheDocument()
    fireEvent.click(screen.getByText('在线只读评测（受后端安全限额保护）'))
    expect(screen.getByText(/不会进入场景流程、审批、Action Gateway 或企业写工具/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建运行' })).toBeDisabled()
    fireEvent.click(screen.getByText('我确认允许受限的外部只读调用'))
    expect(screen.getByRole('button', { name: '创建运行' })).not.toBeDisabled()
  })

  it('creates a run with no browser-calculated metrics and routes to its safe detail view', async () => {
    vi.mocked(evaluationApi.createEvaluationRun).mockResolvedValue({
      run_id: 'run-1', suite_key: 'v5_procurement', suite_name: '采购安全套件', suite_version: '2026.1', content_digest: 'sha256:abc', selected_case_count: 9, mode: 'CONTRACT_ONLY', status: 'PENDING', created_by: 'operator', total_case_count: 0, completed_case_count: 0, passed_case_count: 0, failed_case_count: 0, skipped_case_count: 0, pass_rate: null, call_count: 0, input_token_count: null, output_token_count: null, embedding_text_count: null, estimated_cost: null, price_configuration_version: null, safe_error_code: null, safe_error_summary: null, created_at: '2026-07-27T08:00:00Z', started_at: null, finished_at: null, version: 1,
    })
    vi.mocked(evaluationApi.getEvaluationRun).mockResolvedValue({
      ...(await vi.mocked(evaluationApi.createEvaluationRun)({ suite_key: 'v5_procurement', mode: 'CONTRACT_ONLY', confirm_live_external_calls: false }, 'fixture')),
    })
    vi.mocked(evaluationApi.listEvaluationCases).mockResolvedValue({ items: [], page: 1, page_size: 100, total: 0 })
    renderPage()
    await screen.findByText('最近评测运行')
    fireEvent.click(screen.getByRole('button', { name: '新建评测运行' }))
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(evaluationApi.createEvaluationRun).toHaveBeenCalledWith(expect.objectContaining({ mode: 'CONTRACT_ONLY', confirm_live_external_calls: false }), expect.any(String)))
  })
})
