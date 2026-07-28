import type {
  KnowledgeIndexJobStatus,
  KnowledgeIndexStatus,
  KnowledgePublicationStatus,
  KnowledgeTrustLevel,
} from '../api/contracts'

export const knowledgeSpaceOptions: { value: string; label: string }[] = [
  { value: 'access_and_security', label: '权限与信息安全' },
  { value: 'equipment_maintenance', label: '工业设备维修' },
  { value: 'employee_services', label: '员工服务' },
  { value: 'procurement', label: '采购管理' },
  { value: 'quality_management', label: '质量管理' },
  { value: 'contract_compliance', label: '合同与合规' },
]

const defaultSearchQuestions: Record<string, string> = {
  access_and_security: 'VPN远程访问需要经过谁审批？',
  equipment_maintenance: '冲压设备异常振动时应该如何排查和报修？',
  employee_services: '员工差旅费用报销需要提交哪些材料？',
  procurement: '办公设备采购申请需要经过哪些审批？',
  quality_management: '发现产品质量异常后应该如何处理？',
  contract_compliance: '合同签署前需要完成哪些合规审查？',
}

const knowledgeSpaceLabels = Object.fromEntries(
  knowledgeSpaceOptions.map(({ value, label }) => [value, label]),
)

export function knowledgeSpaceLabel(value: string): string {
  return knowledgeSpaceLabels[value] || '其他文档类别'
}

export function defaultKnowledgeSearchQuestion(value: string): string {
  return defaultSearchQuestions[value] || '请概括该文档类别的重要制度要求。'
}

export const publicationStatusView: Record<
  KnowledgePublicationStatus,
  { label: string; color: string }
> = {
  DRAFT: { label: '草稿', color: 'gold' },
  PUBLISHED: { label: '已发布', color: 'green' },
  RETIRED: { label: '已下线', color: 'default' },
}

export const indexStatusView: Record<
  KnowledgeIndexStatus,
  { label: string; color: string }
> = {
  PENDING: { label: '等待索引', color: 'blue' },
  INDEXING: { label: '正在索引', color: 'cyan' },
  INDEXED: { label: '索引完成', color: 'green' },
  FAILED: { label: '索引失败', color: 'red' },
}

export const indexJobStatusView: Record<
  KnowledgeIndexJobStatus,
  { label: string; color: string }
> = {
  PENDING: { label: '等待处理', color: 'blue' },
  RUNNING: { label: '处理中', color: 'cyan' },
  SUCCEEDED: { label: '处理成功', color: 'green' },
  FAILED: { label: '处理失败', color: 'red' },
}

export const trustLevelLabel: Record<KnowledgeTrustLevel, string> = {
  LOW: '普通',
  MEDIUM: '可信',
  HIGH: '高可信',
  AUTHORITATIVE: '权威制度',
}

const eventLabels: Record<string, string> = {
  CREATED: '知识文档已创建',
  UPLOADED: '原文件已安全上传',
  INDEXING_STARTED: '开始建立知识索引',
  INDEXING_SUCCEEDED: '知识索引建立成功',
  INDEXING_FAILED: '知识索引建立失败',
  INDEX_RETRY_REQUESTED: '已申请重新索引',
  PUBLISHED: '知识文档已发布',
  RETIRED: '知识文档已下线',
  VERSION_SUPERSEDED: '知识版本已被替代',
  VECTOR_CLEANUP_SUCCEEDED: '派生向量清理成功',
  VECTOR_CLEANUP_FAILED: '派生向量清理失败',
}

export function knowledgeEventLabel(eventType: string): string {
  return eventLabels[eventType] || '知识状态发生变化'
}

export function formatKnowledgeDate(value: string | null): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '—'
}
