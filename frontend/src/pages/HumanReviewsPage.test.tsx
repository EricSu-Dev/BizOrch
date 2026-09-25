import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { decideHumanReview, listHumanReviews } from '../api/humanReviews'
import { HumanReviewsPage } from './HumanReviewsPage'

vi.mock('../api/humanReviews', () => ({
  listHumanReviews: vi.fn(),
  decideHumanReview: vi.fn(),
}))

describe('HumanReviewsPage', () => {
  beforeEach(() => {
    vi.mocked(listHumanReviews).mockResolvedValue([{
      workflow_run_id: 'review-run',
      ticket_id: 'ticket-1',
      scenario_key: 'access_management',
      title: '权限申请',
      requester_id: 'EMP-1001',
      can_confirm_success: true,
      version: 2,
      updated_at: '2026-09-24T12:00:00Z',
    }])
    vi.mocked(decideHumanReview).mockResolvedValue()
  })

  it('submits a version-bound public note without closing the workflow', async () => {
    render(<HumanReviewsPage />)
    fireEvent.click(await screen.findByRole('button', { name: /权限申请.*V2/ }))
    fireEvent.change(screen.getByLabelText('公开处理说明'), {
      target: { value: '正在人工核对外部系统的最终状态。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提交处理结果' }))
    await waitFor(() => expect(decideHumanReview).toHaveBeenCalledWith(
      'review-run',
      {
        expected_workflow_version: 2,
        outcome: 'NOTE',
        public_summary: '正在人工核对外部系统的最终状态。',
        evidence_reference: undefined,
      },
    ))
  })
})
