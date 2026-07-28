import type {
  EvaluationBadCaseSeverity,
  EvaluationBadCaseStatus,
  EvaluationCaseStatus,
  EvaluationCategory,
  EvaluationRunMode,
  EvaluationRunStatus,
} from '../api/contracts'

type TagView = { label: string; color: string }

const evaluationSuiteName: Record<string, string> = {
  v2_agent_rag: '多场景智能体与知识检索评测',
  v3_knowledge_governance: '企业知识治理评测',
  v4_employee_lifecycle: '员工生命周期协同评测',
  v5_procurement: '采购与办公申请评测',
  v61_procurement_challenge: '采购智能体与知识检索挑战评测',
}

const evaluationCaseName: Record<string, string> = {
  plan_access_crm_complete: 'CRM 只读权限申请（信息完整）',
  plan_access_erp_complete: 'ERP 标准权限申请（信息完整）',
  plan_access_missing_fields: '权限申请缺少必要信息',
  plan_access_one_month: '一个月临时权限申请',
  plan_access_ninety_days: '九十天临时权限申请',
  plan_knowledge_vpn_approval: '远程访问需要审批',
  plan_knowledge_access_duration: '权限有效期制度问答',
  plan_knowledge_shared_account: '共享账号禁止使用',
  plan_knowledge_agent_write_boundary: '智能体写操作边界',
  plan_unknown_weather: '无关天气请求识别',
  plan_unknown_procurement: '未支持采购请求识别',
  plan_maintenance_motor_overheat: '设备电机过热报修',
  plan_injection_direct_grant: '阻止直接授予权限注入',
  plan_other_employee_identity: '阻止冒用其他员工身份',
  plan_access_no_reason: '权限申请缺少业务理由',
  plan_maintenance_press_complete: '冲压设备完整报修',
  plan_maintenance_missing_observation: '设备报修缺少观察信息',
  plan_maintenance_explicit_danger: '设备明确危险情况处置',
  plan_knowledge_energy_isolation: '设备能源隔离制度问答',
  plan_injection_direct_maintenance_write: '阻止直接创建维修工单注入',
  rag_temporary_access_limit: '临时权限期限检索',
  rag_manager_approval: '直属负责人审批检索',
  rag_shared_account_forbidden: '共享账号禁止制度检索',
  rag_agent_cannot_write: '智能体不得直接写入制度检索',
  rag_unknown_result_no_retry: '结果未知时不得盲目重试',
  rag_crm_read_only_example: 'CRM 只读权限申请示例',
  rag_vpn_mfa: '远程访问多因素认证要求',
  rag_personal_cloud_forbidden: '个人网盘禁止存储企业数据',
  rag_terminal_security: '远程终端安全要求',
  rag_approval_version_binding: '审批与操作版本绑定',
  rag_maintenance_required_report_fields: '设备报修必填字段',
  rag_maintenance_direct_danger: '设备危险情况立即处置',
  rag_maintenance_press_vibration: '冲压设备异常振动排查',
  rag_maintenance_duplicate_order: '避免重复创建维修工单',
  rag_maintenance_status_not_physical_stop: '系统状态不等于物理停机',
  safety_reject_employee_id: '拒绝注入申请员工编号',
  safety_reject_approval_id: '拒绝注入审批编号',
  safety_reject_action_type: '拒绝注入权限操作类型',
  safety_reject_negative_duration: '拒绝负数授权期限',
  safety_accept_bounded_fields: '允许白名单权限申请字段',
  safety_maintenance_reject_requester_id: '拒绝注入报修员工编号',
  safety_maintenance_reject_manager_id: '拒绝注入设备负责人',
  safety_maintenance_reject_action_type: '拒绝注入维修写操作类型',
  safety_maintenance_reject_invalid_impact: '拒绝无效生产影响等级',
  safety_maintenance_accept_bounded_fields: '允许白名单设备报修字段',
  governance_published_employee_visible: '已发布文档对员工可检索',
  governance_draft_hidden: '草稿文档不可检索',
  governance_unindexed_hidden: '未完成索引文档不可检索',
  governance_future_hidden: '未生效文档不可检索',
  governance_expired_hidden: '已失效文档不可检索',
  governance_admin_role_denied: '未授权管理员角色不可检索',
  governance_admin_role_allowed: '授权管理员角色可检索',
  governance_user_allowlist_allowed: '白名单用户可检索',
  governance_user_allowlist_denied: '非白名单用户不可检索',
  governance_only_current_version_visible: '仅当前版本文档可检索',
  governance_retired_hidden: '已下线文档不可检索',
  governance_wrong_space_hidden: '错误文档类别不可检索',
  'v4-onboarding-account-activation': '入职账号须在全部前置步骤完成后启用',
  'v4-transfer-baseline-diff': '调岗按岗位基线差异调整权限',
  'v4-offboarding-no-auto-rollback': '离职安全操作不得自动回滚',
  'v4-job-baseline-minimum-privilege': '岗位基线遵循最小权限原则',
  'v5-plan-office-supplies-complete': '办公耗材采购申请（信息完整）',
  'v5-plan-procurement-injection': '阻止采购写操作注入',
  'v5-plan-procurement-missing-fields': '采购申请缺少必要信息',
  'v5-rag-procurement-required-fields': '采购申请必填字段检索',
  'v5-rag-budget-recheck': '采购执行前预算复核检索',
  'v5-rag-emergency-no-bypass': '紧急采购不得绕过审批检索',
  'v5-safety-reject-requester-identity': '拒绝注入采购申请人身份',
  'v5-safety-reject-policy-and-action': '拒绝注入采购规则与写操作',
  'v5-safety-accept-bounded-fields': '允许白名单采购申请字段',
  'v61-plan-colloquial-supplies': '口语化办公耗材申请识别',
  'v61-plan-typo-procurement': '错别字采购请求识别',
  'v61-plan-missing-authoritative-facts': '缺少权威采购事实时不臆造',
  'v61-plan-injection-and-identity': '阻止采购注入与身份伪造',
  'v61-plan-mixed-access-request': '混合权限诉求下保持采购边界',
  'v61-rag-current-governance': '采购必填项的当前制度检索',
  'v61-rag-final-budget-recheck': '最终执行前预算复核检索',
  'v61-rag-emergency-no-bypass': '紧急采购不得绕过规则检索',
  'v61-rag-cross-space-interference': '跨文档干扰下的采购制度检索',
  'v61-rag-injection-question': '检索问题中的注入指令隔离',
  'v61-safety-reject-sensitive-control-fields': '拒绝敏感控制字段注入',
  'v61-safety-reject-unknown-command-field': '拒绝未知采购命令字段',
}

export function formatEvaluationSuiteName(suiteKey: string, fallback: string): string {
  return evaluationSuiteName[suiteKey] || fallback
}

export function formatEvaluationCaseName(caseId: string): string {
  return evaluationCaseName[caseId] || `评测用例：${caseId}`
}

export const evaluationCategoryView: Record<EvaluationCategory, string> = {
  SUPERVISOR_PLAN: '任务理解与方案',
  RAG_RETRIEVAL: '知识检索',
  KNOWLEDGE_GOVERNANCE: '知识治理',
  SAFETY_SCHEMA: '安全结构校验',
}

export const evaluationRunModeView: Record<EvaluationRunMode, TagView> = {
  CONTRACT_ONLY: { label: '离线合约校验', color: 'blue' },
  LIVE_READ_ONLY: { label: '在线只读评测', color: 'purple' },
}

export const evaluationRunStatusView: Record<EvaluationRunStatus, TagView> = {
  PENDING: { label: '等待执行', color: 'gold' },
  RUNNING: { label: '执行中', color: 'processing' },
  COMPLETED: { label: '已完成', color: 'success' },
  FAILED: { label: '执行失败', color: 'error' },
}

export const evaluationCaseStatusView: Record<EvaluationCaseStatus, TagView> = {
  PASSED: { label: '通过', color: 'success' },
  FAILED: { label: '未通过', color: 'error' },
  SKIPPED: { label: '已跳过', color: 'default' },
  ERROR: { label: '执行异常', color: 'warning' },
}

export const evaluationBadCaseStatusView: Record<EvaluationBadCaseStatus, TagView> = {
  OPEN: { label: '待处理', color: 'error' },
  IN_PROGRESS: { label: '处理中', color: 'processing' },
  READY_FOR_RETEST: { label: '待复测', color: 'gold' },
  VERIFIED: { label: '已验证', color: 'cyan' },
  CLOSED: { label: '已关闭', color: 'success' },
}

export const evaluationSeverityView: Record<EvaluationBadCaseSeverity, TagView> = {
  LOW: { label: '低', color: 'default' },
  MEDIUM: { label: '中', color: 'gold' },
  HIGH: { label: '高', color: 'orange' },
  CRITICAL: { label: '严重', color: 'error' },
}

export function formatEvaluationDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function formatRate(value: number | null): string {
  return value === null ? '尚无结果' : `${(value * 100).toFixed(1)}%`
}

export function newClientCommandKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `evaluation-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
