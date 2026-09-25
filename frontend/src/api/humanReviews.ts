import { apiClient } from './client'

export interface HumanReviewItem {
  workflow_run_id: string
  ticket_id: string
  scenario_key: string
  title: string
  requester_id: string
  can_confirm_success: boolean
  version: number
  updated_at: string
}

export type HumanReviewOutcome =
  | 'NOTE'
  | 'CONFIRMED_SUCCESS'
  | 'CONFIRMED_FAILURE'
  | 'CANCELLED'

export interface HumanReviewDecision {
  expected_workflow_version: number
  outcome: HumanReviewOutcome
  public_summary: string
  evidence_reference?: string
}

export async function listHumanReviews(): Promise<HumanReviewItem[]> {
  const { data } = await apiClient.get<HumanReviewItem[]>('/human-reviews')
  return data
}

export async function decideHumanReview(
  workflowRunId: string,
  decision: HumanReviewDecision,
): Promise<void> {
  await apiClient.post(`/human-reviews/${workflowRunId}/decisions`, decision)
}
