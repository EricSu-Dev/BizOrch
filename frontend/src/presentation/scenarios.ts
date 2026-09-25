export type SafeDisplayField = {
  key: string
  label: string
  value: unknown
}

type ScenarioView = {
  label: string
  serviceLabel: string
  conversationLabel: string
  summaryFields: ReadonlyArray<{ key: string; label: string }>
}

type ActionView = {
  title: string
  operationLabel: string
  parameterFields: ReadonlyArray<{ key: string; label: string }>
}

const scenarios: Record<string, ScenarioView> = {
  access_management: {
    label: '企业系统权限申请',
    serviceLabel: '系统权限服务',
    conversationLabel: '权限服务申请',
    summaryFields: [
      { key: 'manager_id', label: '直属审批人' },
      { key: 'existing_role_codes', label: '已有权限角色' },
    ],
  },
  equipment_maintenance: {
    label: '工业设备报修与维护',
    serviceLabel: '设备维修服务',
    conversationLabel: '设备报修申请',
    summaryFields: [
      { key: 'equipment_code', label: '设备编号' },
      { key: 'equipment_name', label: '设备名称' },
      { key: 'equipment_status', label: '设备状态' },
      { key: 'equipment_version', label: '设备状态版本' },
      { key: 'criticality', label: '设备关键级别' },
      { key: 'responsible_manager_id', label: '设备负责人' },
      { key: 'recent_maintenance_count', label: '近期维修记录数' },
      { key: 'production_impact', label: '生产影响' },
      { key: 'risk_level', label: '确定性风险级别' },
      { key: 'approval_route', label: '审批路由' },
    ],
  },
  employee_lifecycle: {
    label: '员工入职、调岗与离职协同',
    serviceLabel: '员工生命周期服务',
    conversationLabel: '员工生命周期办理',
    summaryFields: [
      { key: 'subject_employee_id', label: '目标员工' },
      { key: 'target_department_code', label: '目标部门' },
      { key: 'target_job_code', label: '目标岗位' },
      { key: 'target_manager_id', label: '直属负责人' },
      { key: 'work_location_code', label: '办公地点' },
    ],
  },
  procurement: {
    label: '采购与办公申请',
    serviceLabel: '采购办公服务',
    conversationLabel: '采购办公申请',
    summaryFields: [
      { key: 'item_summary', label: '申请物品' },
      { key: 'estimated_total_amount', label: '预计申请金额' },
      { key: 'cost_center_code', label: '成本中心' },
      { key: 'desired_date', label: '期望到货日期' },
      { key: 'business_reason', label: '业务理由' },
    ],
  },
}

const actions: Record<string, ActionView> = {
  grant_application_access: {
    title: '权限操作审批',
    operationLabel: '授予企业系统权限',
    parameterFields: [
      { key: 'employee_id', label: '申请员工' },
      { key: 'application_code', label: '目标系统' },
      { key: 'role_code', label: '权限角色' },
      { key: 'duration_days', label: '有效天数' },
      { key: 'business_reason', label: '业务理由' },
    ],
  },
  create_maintenance_work_order: {
    title: '设备维修工单审批',
    operationLabel: '创建设备维修工单',
    parameterFields: [
      { key: 'requester_id', label: '报修人' },
      { key: 'equipment_code', label: '设备编号' },
      { key: 'expected_equipment_version', label: '预期设备版本' },
      { key: 'fault_description', label: '故障现象' },
      { key: 'observed_at', label: '发现时间' },
      { key: 'production_impact', label: '生产影响' },
      { key: 'safety_observation', label: '安全现象' },
      { key: 'business_reason', label: '业务理由' },
      { key: 'priority', label: '工单优先级' },
    ],
  },
  create_pending_employee: {
    title: '员工入职操作计划', operationLabel: '创建待入职员工档案',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'display_name', label: '员工姓名' }, { key: 'department_code', label: '目标部门' }, { key: 'job_code', label: '目标岗位' }, { key: 'manager_id', label: '直属负责人' }, { key: 'work_location_code', label: '办公地点' }],
  },
  create_disabled_corporate_account: {
    title: '员工入职操作计划', operationLabel: '创建禁用状态企业账号',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'initial_status', label: '初始账号状态' }],
  },
  assign_baseline_access_package: {
    title: '员工入职操作计划', operationLabel: '配置岗位基线权限',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'package_code', label: '岗位权限包' }],
  },
  create_asset_assignment_task: {
    title: '员工入职操作计划', operationLabel: '创建办公资产配发任务',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'asset_profile_code', label: '资产配置' }],
  },
  activate_employee_and_account: {
    title: '员工入职操作计划', operationLabel: '激活员工与企业账号',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }],
  },
  update_employee_assignment: {
    title: '员工调岗操作计划', operationLabel: '更新员工组织与岗位',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'department_code', label: '目标部门' }, { key: 'job_code', label: '目标岗位' }, { key: 'manager_id', label: '直属负责人' }, { key: 'work_location_code', label: '办公地点' }],
  },
  revoke_obsolete_baseline_access: {
    title: '员工调岗操作计划', operationLabel: '撤销不适用的旧权限',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'role_bindings', label: '撤销权限' }],
  },
  grant_target_baseline_access: {
    title: '员工调岗操作计划', operationLabel: '授予目标岗位新增权限',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'package_code', label: '岗位权限包' }, { key: 'role_bindings', label: '新增权限' }],
  },
  create_asset_adjustment_task: {
    title: '员工调岗操作计划', operationLabel: '创建办公资产调整任务',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'asset_profile_code', label: '资产配置' }],
  },
  verify_employee_transfer_consistency: {
    title: '员工调岗操作计划', operationLabel: '核对调岗后企业事实',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }],
  },
  disable_corporate_account: {
    title: '员工离职操作计划', operationLabel: '停用企业账号',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }],
  },
  revoke_all_employee_access: {
    title: '员工离职操作计划', operationLabel: '撤销全部有效权限',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }],
  },
  create_asset_return_task: {
    title: '员工离职操作计划', operationLabel: '创建资产归还任务',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'asset_return_note', label: '资产交接说明' }],
  },
  mark_employee_inactive: {
    title: '员工离职操作计划', operationLabel: '标记员工为非在职',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }, { key: 'offboarding_reason', label: '离职原因' }],
  },
  verify_employee_offboarding_consistency: {
    title: '员工离职操作计划', operationLabel: '核对离职后企业事实',
    parameterFields: [{ key: 'subject_employee_id', label: '员工编号' }],
  },
  CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET: {
    title: '办公采购申请审批',
    operationLabel: '创建采购申请并预占预算',
    parameterFields: [
      { key: 'items', label: '申请物品' },
      { key: 'estimated_total_amount', label: '预计申请金额' },
      { key: 'currency', label: '币种' },
      { key: 'cost_center_code', label: '成本中心' },
      { key: 'desired_date', label: '期望到货日期' },
      { key: 'delivery_location_code', label: '收货地点' },
      { key: 'business_reason_summary', label: '业务理由' },
    ],
  },
}

const fieldLabels: Record<string, string> = {
  application_code: '目标系统',
  role_code: '权限角色',
  duration_days: '使用期限',
  business_reason: '业务理由',
  equipment_code: '设备编号',
  fault_description: '故障现象',
  observed_at: '发现时间',
  production_impact: '生产影响',
  safety_observation: '安全现象',
  subject_employee_id: '目标员工',
  target_department_code: '目标部门',
  target_job_code: '目标岗位',
  target_manager_id: '直属负责人',
  work_location_code: '办公地点',
  items: '申请物品',
  estimated_total_amount: '预计申请金额',
  cost_center_code: '成本中心',
  desired_date: '期望到货日期',
  delivery_location_code: '收货地点',
}

const legacyTicketTitles: Record<string, string> = {
  'Enterprise system access request': '企业系统权限申请',
  'Industrial equipment maintenance request': '工业设备报修与维护申请',
  'Employee onboarding request': '员工入职协同申请',
  'Employee transfer request': '员工调岗协同申请',
  'Employee offboarding request': '员工离职协同申请',
  'Office procurement request': '采购与办公申请',
}

const workflowStateLabels: Record<string, string> = {
  CREATED: '已创建',
  RUNNING: '处理中',
  WAITING_USER: '待补充',
  WAITING_APPROVAL: '待审批',
  WAITING_HUMAN: '人工处理',
  EXECUTING: '执行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}

const workflowEventLabels: Record<string, string> = {
  WORKFLOW_CREATED: '服务流程已创建',
  REQUEST_PROCESSING_STARTED: '开始校验申请信息',
  REQUEST_INFORMATION_REQUIRED: '等待员工补充信息',
  REQUEST_INFORMATION_RECEIVED: '员工已补充申请信息',
  APPROVAL_REQUIRED: '审批任务已创建',
  APPROVAL_APPROVED: '审批已通过',
  APPROVAL_REJECTED: '审批已驳回',
  ACTION_EXECUTION_STARTED: '开始执行授权',
  ACTION_SUCCEEDED: '授权执行并验证成功',
  ACTION_REPLAYED: '复用已验证的幂等结果',
  ACTION_FAILED: '授权执行失败',
  ACTION_TOOL_FAILED: '企业工具执行失败',
  ACTION_RESULT_UNKNOWN: '执行结果等待人工核对',
  ACTION_VERIFICATION_FAILED: '写入后验证未通过',
  ACTION_ALREADY_SATISFIED: '目标权限已经满足',
  ACTION_PRECONDITION_REQUIRES_HUMAN: '执行条件变化，转人工核查',
  HUMAN_REVIEW_REQUIRED: '已转交人工核查',
  REQUEST_DENIED_BY_POLICY: '确定性规则拒绝申请',
  REQUEST_ALREADY_SATISFIED: '申请目标已经满足',
  WORKFLOW_COMPLETED: '服务流程已完成',
  MAINTENANCE_INFORMATION_COLLECTION_STARTED: '开始收集设备报修信息',
  MAINTENANCE_INFORMATION_REQUIRED: '等待补充设备报修信息',
  MAINTENANCE_INFORMATION_RECEIVED: '已收到补充的设备报修信息',
  MAINTENANCE_APPROVAL_REQUIRED: '设备维修审批任务已创建',
  MAINTENANCE_HUMAN_REVIEW_REQUIRED: '设备报修已转人工核查',
  MAINTENANCE_REQUEST_DENIED: '设备报修未通过规则校验',
  MAINTENANCE_ALREADY_ACTIVE: '设备已有进行中的维修',
  MAINTENANCE_ACTION_EXECUTION_STARTED: '开始创建设备维修工单',
  MAINTENANCE_ACTION_PRECONDITION_REQUIRES_HUMAN: '执行条件变化，转人工核查',
  MAINTENANCE_ACTION_SUCCEEDED: '设备维修工单创建并验证成功',
  MAINTENANCE_ACTION_REPLAYED: '复用已验证的维修工单结果',
  MAINTENANCE_ACTION_TOOL_FAILED: '维修工单创建失败',
  MAINTENANCE_ACTION_RESULT_UNKNOWN: '维修工单结果等待人工核对',
  MAINTENANCE_ACTION_VERIFICATION_FAILED: '维修工单写入后验证未通过',
  EMPLOYEE_LIFECYCLE_INFORMATION_COLLECTION_STARTED: '开始收集员工生命周期信息',
  EMPLOYEE_LIFECYCLE_INFORMATION_REQUIRED: '等待补充员工生命周期信息',
  EMPLOYEE_LIFECYCLE_INFORMATION_RECEIVED: '已收到员工生命周期补充信息',
  EMPLOYEE_LIFECYCLE_PLAN_APPROVAL_REQUIRED: '员工生命周期计划审批任务已创建',
  EMPLOYEE_LIFECYCLE_PLAN_EXECUTION_STARTED: '开始执行员工生命周期计划',
  EMPLOYEE_LIFECYCLE_PLAN_COMPLETED: '员工生命周期计划已完成',
  EMPLOYEE_LIFECYCLE_PLAN_REQUIRES_HUMAN: '员工生命周期计划已转人工处理',
  EMPLOYEE_LIFECYCLE_HUMAN_REVIEW_REQUIRED: '员工生命周期请求已转人工处理',
  EMPLOYEE_LIFECYCLE_NO_ACTION: '员工生命周期请求无需办理',
  EMPLOYEE_LIFECYCLE_REQUEST_DENIED: '员工生命周期请求未通过规则校验',
  PROCUREMENT_INFORMATION_COLLECTION_STARTED: '开始收集采购申请信息',
  PROCUREMENT_INFORMATION_REQUIRED: '等待补充采购申请信息',
  PROCUREMENT_INFORMATION_RECEIVED: '已收到补充的采购申请信息',
  PROCUREMENT_READY_FOR_DETERMINISTIC_POLICY: '采购信息已提交规则校验',
  PROCUREMENT_POLICY_INFORMATION_REQUIRED: '采购规则要求补充信息',
  PROCUREMENT_POLICY_HUMAN_REVIEW_REQUIRED: '采购申请已转人工核查',
  PROCUREMENT_APPROVAL_SEQUENCE_REQUIRED: '采购审批路线已创建',
  PROCUREMENT_EXECUTION_STARTED: '开始创建采购申请并预占预算',
  PROCUREMENT_EXECUTION_COMPLETED: '采购申请与预算预占已验证完成',
  PROCUREMENT_EXECUTION_REQUIRES_HUMAN: '采购执行已转人工处理',
  HUMAN_REVIEW_NOTE_RECORDED: '人工核对进展已记录',
  HUMAN_REVIEW_CONCLUDED: '人工核对已结案',
}

const fieldValueLabels: Record<string, Record<string, string>> = {
  equipment_status: {
    RUNNING: '正常运行',
    DEGRADED: '降级运行',
    MAINTENANCE_PENDING: '待维修',
    IN_MAINTENANCE: '维修中',
    OUT_OF_SERVICE: '已停用',
  },
  criticality: {
    LOW: '低',
    MEDIUM: '中',
    HIGH: '高',
  },
  risk_level: {
    LOW: '低',
    MEDIUM: '中',
    HIGH: '高',
  },
  production_impact: {
    NONE: '无生产影响',
    SLOWDOWN: '生产降速',
    STOPPED: '生产停止',
  },
  priority: {
    LOW: '低',
    MEDIUM: '中',
    HIGH: '高',
  },
  approval_route: {
    MANAGER: '直属领导审批',
    APPLICATION_OWNER: '应用负责人审批',
    SECURITY_OFFICER: '信息安全负责人审批',
    SITE_EMERGENCY_RESPONSE: '现场应急处置',
    EQUIPMENT_OPERATIONS_REVIEW: '设备运行负责人复核',
    EQUIPMENT_RESPONSIBLE_MANAGER: '设备责任人审批',
  },
}

function safeFieldValue(field: string, value: unknown): unknown {
  return typeof value === 'string'
    ? fieldValueLabels[field]?.[value] || value
    : value
}

export function scenarioLabel(scenarioKey: string | null | undefined): string {
  return scenarioKey && scenarios[scenarioKey]
    ? scenarios[scenarioKey].label
    : '企业服务请求'
}

export function scenarioServiceLabel(scenarioKey: string | null | undefined): string {
  return scenarioKey && scenarios[scenarioKey]
    ? scenarios[scenarioKey].serviceLabel
    : '企业服务'
}

export function scenarioConversationLabel(scenarioKey: string | null | undefined): string {
  return scenarioKey && scenarios[scenarioKey]
    ? scenarios[scenarioKey].conversationLabel
    : '企业知识会话'
}

export function ticketTitleLabel(title: string): string {
  return legacyTicketTitles[title] || title
}

export function workflowStateLabel(state: string): string {
  return workflowStateLabels[state] || state
}

export function workflowEventLabel(eventType: string): string {
  return workflowEventLabels[eventType] || eventType
}

export function actionPlanTitle(planType: string): string {
  return {
    ONBOARDING: '员工入职操作计划',
    TRANSFER: '员工调岗操作计划',
    OFFBOARDING: '员工离职操作计划',
    OFFICE_PROCUREMENT: '采购与预算预占计划',
  }[planType] || '企业组合操作计划'
}

export function approvalStageLabel(stageCode: string | null | undefined): string {
  return {
    BUSINESS_CONFIRMATION: '业务确认',
    BUDGET_CONFIRMATION: '预算确认',
    PROCUREMENT_CONFIRMATION: '采购复核',
  }[stageCode || ''] || '审批阶段'
}

export function approvalStageStatusLabel(status: string): string {
  return {
    QUEUED: '待后续处理',
    PENDING: '当前待审批',
    APPROVED: '已通过',
    REJECTED: '已驳回',
    CANCELLED: '已取消',
  }[status] || '状态未知'
}

export function approvalStageStatusColor(status: string): string {
  if (status === 'APPROVED') return 'green'
  if (status === 'REJECTED') return 'red'
  if (status === 'PENDING') return 'cyan'
  if (status === 'CANCELLED') return 'default'
  return 'blue'
}

export function actionPlanStatusLabel(status: string): string {
  return {
    DRAFT: '草稿',
    PENDING_APPROVAL: '待审批',
    APPROVED: '已批准',
    EXECUTING: '执行中',
    WAITING_HUMAN: '等待人工处理',
    COMPLETED: '已完成',
    CANCELLED: '已取消',
  }[status] || '状态未知'
}

export function actionPlanStepStatusLabel(status: string): string {
  return {
    PENDING: '待执行',
    BLOCKED: '等待前置步骤',
    EXECUTING: '执行中',
    SUCCEEDED: '已验证成功',
    REPLAYED: '复用已验证结果',
    FAILED: '执行失败',
    RESULT_UNKNOWN: '结果待人工核对',
    VERIFICATION_FAILED: '写后验证未通过',
    COMPENSATION_PENDING: '等待补偿处理',
    COMPENSATED: '已完成补偿',
    COMPENSATION_FAILED: '补偿失败',
    SKIPPED: '已跳过',
  }[status] || '状态未知'
}

export function actionPlanStepStatusColor(status: string): string {
  if (['SUCCEEDED', 'REPLAYED', 'COMPLETED'].includes(status)) return 'green'
  if (['FAILED', 'VERIFICATION_FAILED', 'COMPENSATION_FAILED'].includes(status)) return 'red'
  if (['RESULT_UNKNOWN', 'WAITING_HUMAN', 'COMPENSATION_PENDING'].includes(status)) return 'purple'
  if (['EXECUTING', 'APPROVED'].includes(status)) return 'blue'
  if (['PENDING_APPROVAL', 'PENDING', 'BLOCKED'].includes(status)) return 'cyan'
  return 'default'
}

export function missingFieldLabel(field: string): string {
  return fieldLabels[field] || '其他必要信息'
}

export function safeScenarioSummary(
  scenarioKey: string | null,
  summary: Record<string, unknown> | null,
): SafeDisplayField[] {
  if (!scenarioKey || !summary || !scenarios[scenarioKey]) return []
  return scenarios[scenarioKey].summaryFields.flatMap((field) => {
    const value = summary[field.key]
    return value === null || value === undefined || value === ''
      ? []
      : [{ ...field, value: safeFieldValue(field.key, value) }]
  })
}

export function actionPresentation(actionType: string): ActionView | null {
  return actions[actionType] || null
}

export function safeActionParameters(
  actionType: string,
  parameters: Record<string, unknown>,
): SafeDisplayField[] {
  const action = actions[actionType]
  if (!action) return []
  return action.parameterFields.flatMap((field) => {
    const value = parameters[field.key]
    return value === null || value === undefined || value === ''
      ? []
      : [{ ...field, value: safeFieldValue(field.key, value) }]
  })
}

export function safeDisplayValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) {
    if (value.every((item) => item && typeof item === 'object' && !Array.isArray(item))) {
      const lines = value.flatMap((item) => {
        const record = item as Record<string, unknown>
        const name = record.item_name
        const quantity = record.quantity
        return typeof name === 'string' && (typeof quantity === 'string' || typeof quantity === 'number')
          ? [`${name} × ${quantity}`]
          : []
      })
      return lines.length ? lines.join('、') : '—'
    }
    const safe = value.filter((item) => typeof item === 'string' || typeof item === 'number')
    return safe.length ? safe.join('、') : '—'
  }
  return '—'
}
