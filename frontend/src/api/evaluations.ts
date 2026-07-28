import { apiClient } from './client'
import type {
  EvaluationBadCase,
  EvaluationBadCasePage,
  EvaluationBadCaseSeverity,
  EvaluationBaseline,
  EvaluationBaselinePage,
  EvaluationCaseResultPage,
  EvaluationCaseStatus,
  EvaluationCategory,
  EvaluationComparison,
  EvaluationRun,
  EvaluationRunMode,
  EvaluationRunPage,
  EvaluationSuitePage,
} from './contracts'

export interface CreateEvaluationRunInput {
  suite_key: string
  mode: EvaluationRunMode
  case_ids?: string[]
  confirm_live_external_calls: boolean
}

export async function listEvaluationSuites(): Promise<EvaluationSuitePage> {
  const { data } = await apiClient.get<EvaluationSuitePage>('/evaluations/suites')
  return data
}

export async function listEvaluationRuns(): Promise<EvaluationRunPage> {
  const { data } = await apiClient.get<EvaluationRunPage>('/evaluations/runs', {
    params: { page: 1, page_size: 30 },
  })
  return data
}

export async function getEvaluationRun(runId: string): Promise<EvaluationRun> {
  const { data } = await apiClient.get<EvaluationRun>(`/evaluations/runs/${runId}`)
  return data
}

export async function createEvaluationRun(
  input: CreateEvaluationRunInput,
  idempotencyKey: string,
): Promise<EvaluationRun> {
  const { data } = await apiClient.post<EvaluationRun>('/evaluations/runs', input, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  return data
}

export async function listEvaluationCases(
  runId: string,
  filters: { status?: EvaluationCaseStatus; category?: EvaluationCategory } = {},
): Promise<EvaluationCaseResultPage> {
  const { data } = await apiClient.get<EvaluationCaseResultPage>(
    `/evaluations/runs/${runId}/cases`,
    { params: { page: 1, page_size: 100, ...filters } },
  )
  return data
}

export async function setEvaluationBaseline(runId: string): Promise<EvaluationBaseline> {
  const { data } = await apiClient.post<EvaluationBaseline>(
    `/evaluations/runs/${runId}/baseline`,
    { confirm: true },
  )
  return data
}

export async function listEvaluationBaselines(): Promise<EvaluationBaselinePage> {
  const { data } = await apiClient.get<EvaluationBaselinePage>('/evaluations/baselines', {
    params: { page: 1, page_size: 20 },
  })
  return data
}

export async function getEvaluationComparison(runId: string): Promise<EvaluationComparison> {
  const { data } = await apiClient.get<EvaluationComparison>(
    `/evaluations/runs/${runId}/comparison`,
  )
  return data
}

export async function archiveEvaluationBadCase(
  runId: string,
  caseId: string,
  input: { severity: EvaluationBadCaseSeverity; safe_issue_summary: string },
): Promise<EvaluationBadCase> {
  const { data } = await apiClient.post<EvaluationBadCase>(
    `/evaluations/runs/${runId}/cases/${caseId}/bad-case`, input,
  )
  return data
}

export async function listEvaluationBadCases(): Promise<EvaluationBadCasePage> {
  const { data } = await apiClient.get<EvaluationBadCasePage>('/evaluations/bad-cases', {
    params: { page: 1, page_size: 50 },
  })
  return data
}

export async function updateEvaluationBadCase(
  badCaseId: string,
  body: Record<string, unknown>,
): Promise<EvaluationBadCase> {
  const { data } = await apiClient.patch<EvaluationBadCase>(
    `/evaluations/bad-cases/${badCaseId}`, body,
  )
  return data
}

export async function createEvaluationRetest(
  badCaseId: string,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<EvaluationRun> {
  const { data } = await apiClient.post<EvaluationRun>(
    `/evaluations/bad-cases/${badCaseId}/retest`,
    { expected_version: expectedVersion },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  return data
}

export async function verifyEvaluationBadCase(
  badCaseId: string,
  expectedVersion: number,
): Promise<EvaluationBadCase> {
  const { data } = await apiClient.post<EvaluationBadCase>(
    `/evaluations/bad-cases/${badCaseId}/verify`,
    { expected_version: expectedVersion },
  )
  return data
}

export async function closeEvaluationBadCase(
  badCaseId: string,
  expectedVersion: number,
): Promise<EvaluationBadCase> {
  const { data } = await apiClient.post<EvaluationBadCase>(
    `/evaluations/bad-cases/${badCaseId}/close`,
    { expected_version: expectedVersion },
  )
  return data
}
