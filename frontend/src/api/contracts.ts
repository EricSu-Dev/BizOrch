export type RoleName = 'employee' | 'approver' | 'operator' | 'hr' | 'admin'
export type AvatarKey =
  | 'person'
  | 'operations'
  | 'approval'
  | 'shield'
  | 'maintenance'
  | 'robot'

export interface AuthUser {
  employee_id: string
  username: string
  roles: RoleName[]
  avatar_key?: AvatarKey | null
  has_custom_avatar?: boolean
  avatar_version?: string | null
  avatar_url?: string | null
}

export interface LoginResult {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  user: AuthUser
}

export interface KnowledgeCitation {
  document_id: string
  chunk_id: string
  chunk_index: number
  title: string
  source_uri: string
  version_label: string
  source_department: string
  trust_level: KnowledgeTrustLevel
  excerpt: string
  vector_score: number
  lexical_score: number
  combined_score: number
}

export type KnowledgeTrustLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'AUTHORITATIVE'
export type KnowledgePublicationStatus = 'DRAFT' | 'PUBLISHED' | 'RETIRED'
export type KnowledgeIndexStatus = 'PENDING' | 'INDEXING' | 'INDEXED' | 'FAILED'
export type KnowledgeIndexJobStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
export type KnowledgeSourceFormat = 'MARKDOWN' | 'TEXT' | 'PDF'

export interface KnowledgeDocument {
  document_id: string
  knowledge_space: string
  title: string
  source_uri: string
  version_label: string
  source_department: string
  trust_level: KnowledgeTrustLevel
  effective_from: string
  effective_until: string | null
  allowed_roles: string[]
  allowed_user_ids: string[]
  publication_status: KnowledgePublicationStatus
  index_status: KnowledgeIndexStatus
  index_version: string
  chunk_count: number
  published_at: string | null
  published_by: string | null
  retired_at: string | null
  retired_by: string | null
  supersedes_document_id: string | null
  original_filename: string | null
  source_format: KnowledgeSourceFormat | null
  source_size_bytes: number | null
  created_at: string
  updated_at: string
}

export interface KnowledgeIndexJob {
  job_id: string
  document_id: string
  index_version: string
  status: KnowledgeIndexJobStatus
  attempt_count: number
  max_attempts: number
  available_at: string
  lease_owner: string | null
  lease_expires_at: string | null
  last_error_code: string | null
  last_error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeChunkSummary {
  chunk_id: string
  chunk_index: number
  char_count: number
  excerpt: string
}

export interface KnowledgeDocumentDetail {
  document: KnowledgeDocument
  chunks: KnowledgeChunkSummary[]
  latest_index_job: KnowledgeIndexJob | null
}

export interface KnowledgeDocumentEvent {
  event_id: string
  document_id: string
  event_type: string
  actor_id: string
  actor_roles: string[]
  safe_payload: Record<string, unknown>
  created_at: string
}

export interface PageResult<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export type KnowledgeDocumentPage = PageResult<KnowledgeDocument>
export type KnowledgeIndexJobPage = PageResult<KnowledgeIndexJob>
export type KnowledgeDocumentEventPage = PageResult<KnowledgeDocumentEvent>

export interface KnowledgeUploadResult {
  document: KnowledgeDocument
  index_job: KnowledgeIndexJob
}

export interface KnowledgeLifecycleResult {
  document: KnowledgeDocument
  retired_document_ids: string[]
  vector_cleanup_failed_document_ids: string[]
}

export interface KnowledgeSearchResult {
  retrieval_id: string
  knowledge_space: string
  query: string
  citations: KnowledgeCitation[]
}

export type EvaluationCategory =
  | 'SUPERVISOR_PLAN'
  | 'RAG_RETRIEVAL'
  | 'KNOWLEDGE_GOVERNANCE'
  | 'SAFETY_SCHEMA'
export type EvaluationRunMode = 'CONTRACT_ONLY' | 'LIVE_READ_ONLY'
export type EvaluationRunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
export type EvaluationCaseStatus = 'PASSED' | 'FAILED' | 'SKIPPED' | 'ERROR'
export type EvaluationBadCaseStatus =
  | 'OPEN'
  | 'IN_PROGRESS'
  | 'READY_FOR_RETEST'
  | 'VERIFIED'
  | 'CLOSED'
export type EvaluationBadCaseSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface EvaluationSuite {
  suite_key: string
  suite_name: string
  suite_version: string
  content_digest: string
  case_count: number
  categories: EvaluationCategory[]
  supported_modes: EvaluationRunMode[]
}

export interface EvaluationRun {
  run_id: string
  suite_key: string
  suite_name: string
  suite_version: string
  content_digest: string
  selected_case_count: number
  mode: EvaluationRunMode
  status: EvaluationRunStatus
  created_by: string
  total_case_count: number
  completed_case_count: number
  passed_case_count: number
  failed_case_count: number
  skipped_case_count: number
  pass_rate: number | null
  call_count: number
  input_token_count: number | null
  output_token_count: number | null
  embedding_text_count: number | null
  estimated_cost: number | null
  price_configuration_version: string | null
  safe_error_code: string | null
  safe_error_summary: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  version: number
}

export interface EvaluationCaseResult {
  result_id: string
  run_id: string
  case_id: string
  category: EvaluationCategory
  status: EvaluationCaseStatus
  failure_code: string | null
  safe_failure_summary: string | null
  result_summary: string | null
  duration_ms: number | null
  call_count: number
  input_token_count: number | null
  output_token_count: number | null
  created_at: string
  updated_at: string
}

export interface EvaluationBaseline {
  baseline_id: string
  run_id: string
  suite_key: string
  suite_version: string
  content_digest: string
  mode: EvaluationRunMode
  is_current: boolean
  set_by: string
  created_at: string
  version: number
}

export interface EvaluationComparison {
  baseline_run_id: string
  current_run_id: string
  differences: Array<{
    case_id: string
    difference: string
    baseline_status: EvaluationCaseStatus | null
    current_status: EvaluationCaseStatus | null
  }>
  regression_count: number
  improvement_count: number
  persistent_failure_count: number
  error_or_skipped_count: number
  duration_p50_ms: number | null
  duration_p95_ms: number | null
}

export interface EvaluationBadCase {
  bad_case_id: string
  source_run_id: string
  source_case_id: string
  suite_key: string
  suite_version: string
  content_digest: string
  case_id: string
  category: EvaluationCategory
  status: EvaluationBadCaseStatus
  severity: EvaluationBadCaseSeverity
  assignee_id: string | null
  safe_issue_summary: string
  remediation_note: string | null
  target_fix_version: string | null
  latest_retest_run_id: string | null
  latest_retest_status: string | null
  created_by: string
  created_at: string
  updated_at: string
  version: number
}

export interface EvaluationPageResult<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type EvaluationSuitePage = EvaluationPageResult<EvaluationSuite>
export type EvaluationRunPage = EvaluationPageResult<EvaluationRun>
export type EvaluationCaseResultPage = EvaluationPageResult<EvaluationCaseResult>
export type EvaluationBaselinePage = EvaluationPageResult<EvaluationBaseline>
export type EvaluationBadCasePage = EvaluationPageResult<EvaluationBadCase>

export interface AgentTraceEvent {
  agent_name: string
  capability: string
  status: string
  summary: string
}

export interface AgentWorkflowSnapshot {
  scenario_key: string
  workflow_run_id: string
  service_request_id: string | null
  ticket_id: string | null
  workflow_state: string
  workflow_version: number
  action_plan_id?: string | null
  action_plan_version?: number | null
  approval_id: string | null
  missing_fields: string[]
}

export type AccessWorkflowSnapshot = Omit<AgentWorkflowSnapshot, 'scenario_key'>

export interface MultiAgentResult {
  request_id: string
  intent:
    | 'ACCESS_REQUEST'
    | 'MAINTENANCE_REQUEST'
    | 'EMPLOYEE_ONBOARDING'
    | 'EMPLOYEE_TRANSFER'
    | 'EMPLOYEE_OFFBOARDING'
    | 'KNOWLEDGE_QUESTION'
    | 'UNKNOWN'
  reply: string
  scenario_key: string | null
  knowledge: { citations: KnowledgeCitation[] } | null
  scenario_summary: Record<string, unknown> | null
  workflow: AgentWorkflowSnapshot | null
  trace: AgentTraceEvent[]
}

export interface ConversationTurnResult {
  conversation_id: string
  client_message_id: string
  agent: MultiAgentResult
}

export interface ConversationSummary {
  conversation_id: string
  title: string | null
  scenario_key: string | null
  workflow_run_id: string | null
  status: 'OPEN' | 'CLOSED'
  message_count: number
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  message_id: string
  sequence: number
  role: 'USER' | 'ASSISTANT'
  content: string
  client_message_id: string | null
  agent_result: MultiAgentResult | null
  created_at: string
}

export type TicketStatus =
  | 'OPEN'
  | 'IN_PROGRESS'
  | 'NEEDS_INPUT'
  | 'PENDING_APPROVAL'
  | 'HUMAN_REVIEW'
  | 'RESOLVED'
  | 'FAILED'
  | 'CANCELLED'

export interface TicketEvent {
  sequence: number
  event_type: string
  from_status: TicketStatus | null
  to_status: TicketStatus
  payload: Record<string, unknown>
  created_at: string
}

export interface Ticket {
  ticket_id: string
  service_request_id: string
  workflow_run_id: string
  requester_id: string
  assignee_id: string | null
  scenario_key: string
  title: string
  subject_reference: string | null
  status: TicketStatus
  workflow_state: string
  resolved_at: string | null
  created_at: string
  updated_at: string
  events: TicketEvent[]
}

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type ApprovalDecisionType = 'APPROVE' | 'REJECT'
export type ApprovalSubjectType = 'ACTION' | 'PLAN'

export interface ApprovalPlanStep {
  step_id: string
  step_order: number
  depends_on_step_ids: string[]
  action_id: string
  action_version: number
  action_type: string
  target_resource: string
  parameters: Record<string, unknown>
  content_summary: string
  reversibility: 'REVERSIBLE' | 'MANUAL_ONLY' | 'IRREVERSIBLE'
  compensation_action_type: string | null
}

export interface ApprovalPlan {
  plan_id: string
  plan_version: number
  scenario_key: string
  plan_type: string
  subject_reference: string
  content_summary: string
  steps: ApprovalPlanStep[]
}

export interface ApprovalDecision {
  decision: ApprovalDecisionType
  decided_by: string
  comment: string | null
  created_at: string
}

export interface ApprovalTask {
  approval_id: string
  workflow_run_id: string
  workflow_state: string
  workflow_version: number
  ticket_id: string | null
  requester_id: string | null
  approval_subject_type: ApprovalSubjectType
  action_id: string | null
  action_version: number | null
  action_type: string | null
  target_resource: string | null
  parameters: Record<string, unknown> | null
  content_summary: string
  action_plan: ApprovalPlan | null
  approval_sequence_id?: string | null
  stage_order?: number | null
  stage_code?: string | null
  approval_route?: ApprovalRouteProgress | null
  status: ApprovalStatus
  created_at: string
  decided_at: string | null
  decision: ApprovalDecision | null
}

export type ApprovalSequenceStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED'
export type ApprovalStageStatus = 'QUEUED' | ApprovalStatus | 'CANCELLED'

export interface ApprovalRouteStageProgress {
  approval_id: string
  stage_order: number
  stage_code: string
  approver_id: string
  status: ApprovalStageStatus
  decided_at: string | null
}

export interface ApprovalRouteProgress {
  sequence_id: string
  route_version: number
  status: ApprovalSequenceStatus
  current_stage_order: number | null
  stages: ApprovalRouteStageProgress[]
  completed_at: string | null
}

export interface ApprovalDecisionResult {
  approval_id: string
  approval_status: ApprovalStatus
  scenario_key: string
  workflow_run_id: string
  workflow_state: WorkflowState
  workflow_version: number
  checkpoint_pending: boolean
}

export type WorkflowState =
  | 'CREATED'
  | 'RUNNING'
  | 'WAITING_USER'
  | 'WAITING_APPROVAL'
  | 'WAITING_HUMAN'
  | 'EXECUTING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface WorkflowProgressEvent {
  sequence: number
  event_type: string
  from_state: WorkflowState | null
  to_state: WorkflowState
  created_at: string
}

export type ActionPlanStatus =
  | 'DRAFT'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'EXECUTING'
  | 'WAITING_HUMAN'
  | 'COMPLETED'
  | 'CANCELLED'

export type ActionPlanStepStatus =
  | 'PENDING'
  | 'BLOCKED'
  | 'EXECUTING'
  | 'SUCCEEDED'
  | 'REPLAYED'
  | 'FAILED'
  | 'RESULT_UNKNOWN'
  | 'VERIFICATION_FAILED'
  | 'COMPENSATION_PENDING'
  | 'COMPENSATED'
  | 'COMPENSATION_FAILED'
  | 'SKIPPED'

export interface WorkflowActionPlanStepProgress {
  step_id: string
  step_order: number
  depends_on_step_ids: string[]
  action_type: string
  content_summary: string
  reversibility: 'REVERSIBLE' | 'MANUAL_ONLY' | 'IRREVERSIBLE'
  status: ActionPlanStepStatus
  attempt_count: number
  last_error_code: string | null
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowActionPlanProgress {
  plan_id: string
  plan_version: number
  plan_type: string
  subject_reference: string
  content_summary: string
  status: ActionPlanStatus
  steps: WorkflowActionPlanStepProgress[]
}

export interface WorkflowProgress {
  workflow_run_id: string
  ticket_id: string
  scenario_key: string
  state: WorkflowState
  version: number
  terminal: boolean
  created_at: string
  updated_at: string
  events: WorkflowProgressEvent[]
  action_plan: WorkflowActionPlanProgress | null
  approval_route?: ApprovalRouteProgress | null
  business_summary?: WorkflowBusinessSummary | null
}

export interface WorkflowBusinessSummary {
  kind: string
  fields: Record<string, string>
}
