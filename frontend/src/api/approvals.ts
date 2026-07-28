import { apiClient } from './client'
import type {
  ApprovalDecisionResult,
  ApprovalDecisionType,
  ApprovalStatus,
  ApprovalTask,
} from './contracts'

export async function listApprovals(status?: ApprovalStatus): Promise<ApprovalTask[]> {
  const { data } = await apiClient.get<ApprovalTask[]>('/approvals', {
    params: status ? { status } : undefined,
  })
  return data
}

export async function listApprovalHistory(): Promise<ApprovalTask[]> {
  const [approved, rejected] = await Promise.all([
    listApprovals('APPROVED'),
    listApprovals('REJECTED'),
  ])
  return [...approved, ...rejected].sort(
    (left, right) =>
      new Date(right.decided_at || right.created_at).getTime() -
      new Date(left.decided_at || left.created_at).getTime(),
  )
}

export async function getApproval(approvalId: string): Promise<ApprovalTask> {
  const { data } = await apiClient.get<ApprovalTask>(`/approvals/${approvalId}`)
  return data
}

export async function decideApproval(input: {
  task: ApprovalTask
  decision: ApprovalDecisionType
  comment?: string
}): Promise<ApprovalDecisionResult> {
  const { data } = await apiClient.post<ApprovalDecisionResult>(
    `/approvals/${input.task.approval_id}/decisions`,
    {
      expected_workflow_version: input.task.workflow_version,
      decision: input.decision,
      comment: input.comment || null,
    },
    { timeout: 90_000 },
  )
  return data
}
