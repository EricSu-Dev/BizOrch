import { useEffect, useState } from 'react'
import {
  Button,
  Empty,
  Input,
  Modal,
  Segmented,
  Skeleton,
  Tag,
  message as toast,
} from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { ApprovalRouteProgress } from '../components/ApprovalRouteProgress'
import {
  decideApproval,
  getApproval,
  listApprovalHistory,
  listApprovals,
} from '../api/approvals'
import { isRequestTimeout, publicErrorMessage } from '../api/client'
import type {
  ApprovalDecisionType,
  ApprovalStatus,
  ApprovalTask,
} from '../api/contracts'
import {
  actionPresentation,
  approvalStageLabel,
  safeActionParameters,
  safeDisplayValue,
  workflowStateLabel,
} from '../presentation/scenarios'

type WorkbenchFilter = 'pending' | 'history'

const approvalStatusView: Record<
  ApprovalStatus,
  { label: string; color: string }
> = {
  PENDING: { label: '待审批', color: 'cyan' },
  APPROVED: { label: '已通过', color: 'green' },
  REJECTED: { label: '已驳回', color: 'red' },
}

function formatDate(value: string | null): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '—'
}

function planTitle(planType: string | undefined): string {
  const titles: Record<string, string> = {
    employee_onboarding: '员工入职操作计划审批',
    employee_transfer: '员工调岗操作计划审批',
    employee_offboarding: '员工离职操作计划审批',
    ONBOARDING: '员工入职操作计划审批',
    TRANSFER: '员工调岗操作计划审批',
    OFFBOARDING: '员工离职操作计划审批',
    OFFICE_PROCUREMENT: '办公采购申请审批',
  }
  return planType ? titles[planType] || '企业组合操作计划审批' : '企业组合操作计划审批'
}

function reversibilityLabel(value: string): string {
  return {
    REVERSIBLE: '可按已审批方案人工处理',
    MANUAL_ONLY: '仅支持人工恢复',
    IRREVERSIBLE: '不可逆操作',
  }[value] || '恢复策略未配置'
}

function parameterText(
  parameters: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = parameters?.[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null
}

function accessRoleLabel(roleCode: string | null): string {
  return roleCode === 'read_only' ? '只读权限' : roleCode || '指定权限'
}

function actionTargetLabel(
  actionType: string | null | undefined,
  parameters: Record<string, unknown> | null | undefined,
  fallback: string | null | undefined,
): string {
  if (actionType === 'grant_application_access') {
    const application = parameterText(parameters, 'application_code')
    const role = accessRoleLabel(parameterText(parameters, 'role_code'))
    const days = parameterText(parameters, 'duration_days')
    return [application && `${application} 系统`, role, days && `${days} 天`]
      .filter(Boolean)
      .join(' · ') || '企业系统权限'
  }
  if (actionType === 'CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET') {
    const costCenter = parameterText(parameters, 'cost_center_code')
    return costCenter ? `成本中心 ${costCenter}` : '指定成本中心'
  }
  if (actionType === 'create_maintenance_work_order') {
    const equipment = parameterText(parameters, 'equipment_code')
    return equipment ? `设备 ${equipment}` : '指定设备'
  }
  return fallback || '—'
}

function approvalTarget(task: ApprovalTask): { label: string; value: string } {
  const plan = task.action_plan
  const isProcurement = plan?.plan_type === 'OFFICE_PROCUREMENT'
    || task.action_type === 'CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET'
  if (isProcurement) {
    return {
      label: '预算成本中心',
      value: actionTargetLabel(
        task.action_type,
        task.parameters,
        plan?.subject_reference || task.target_resource,
      ),
    }
  }
  if (task.action_type === 'grant_application_access') {
    return {
      label: '授权范围',
      value: actionTargetLabel(task.action_type, task.parameters, task.target_resource),
    }
  }
  return {
    label: plan ? '目标员工' : '目标资源',
    value: plan?.subject_reference || actionTargetLabel(
      task.action_type,
      task.parameters,
      task.target_resource,
    ),
  }
}

function approvalSummary(task: ApprovalTask): string {
  if (task.action_type === 'grant_application_access') {
    const employee = parameterText(task.parameters, 'employee_id')
    const target = actionTargetLabel(task.action_type, task.parameters, task.target_resource)
    return employee ? `为 ${employee} 授予 ${target}` : `授予 ${target}`
  }
  return task.content_summary
}

function workflowRevisionLabel(version: number): string {
  return version <= 0 ? '初始记录' : `第 ${version} 次状态更新`
}

function ApprovalListItem({
  task,
  active,
  onClick,
}: {
  task: ApprovalTask
  active: boolean
  onClick: () => void
}) {
  const status = approvalStatusView[task.status]
  const action = task.action_type ? actionPresentation(task.action_type) : undefined
  const title = task.action_plan
    ? planTitle(task.action_plan.plan_type)
    : action?.title || '企业操作审批'
  return (
    <button
      type="button"
      className={`approval-list-item ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      <div><span>{title}</span><Tag color={status.color}>{status.label}</Tag></div>
      <strong>{approvalSummary(task)}</strong>
      <p>{task.action_plan ? '发起人' : '申请人'} {task.requester_id || '未知'}</p>
      <small>{formatDate(task.decided_at || task.created_at)}</small>
    </button>
  )
}

function ApprovalDetail({
  task,
  onDecision,
}: {
  task: ApprovalTask
  onDecision: (decision: ApprovalDecisionType) => void
}) {
  const status = approvalStatusView[task.status]
  const plan = task.action_plan
  const action = task.action_type ? actionPresentation(task.action_type) : undefined
  const safeParameters = task.action_type
    ? safeActionParameters(task.action_type, task.parameters || {})
    : []
  const boundVersion = plan?.plan_version ?? task.action_version
  const title = plan ? planTitle(plan.plan_type) : action?.title || '企业操作审批'
  const currentResponsibility = task.approval_route && task.stage_code
    ? approvalStageLabel(task.stage_code)
    : null
  const target = approvalTarget(task)
  return (
    <div className="approval-detail">
      <header className="approval-detail-header">
        <div>
          <span className="eyebrow">APPROVAL TASK</span>
          <h1>{title}</h1>
          <p>{approvalSummary(task)}</p>
        </div>
        <Tag color={status.color}>{status.label}</Tag>
      </header>

      <section className="approval-warning">
        <div className="version-badge">V{boundVersion}</div>
        <div>
          <strong>
            审批绑定{plan ? '组合操作计划' : '操作方案'}第 {boundVersion} 版
          </strong>
          <p>
            {plan
              ? '计划中的步骤、依赖关系或版本发生变化后，本次审批不会被用于执行新计划。'
              : '操作内容或版本发生变化后，本次审批不会被用于执行新方案。'}
          </p>
        </div>
      </section>

      {currentResponsibility && (
        <p className="approval-current-responsibility">当前审批职责：{currentResponsibility}</p>
      )}

      <section className="approval-overview">
        <div><span>{plan ? '发起人' : '申请员工'}</span><strong>{task.requester_id || '—'}</strong></div>
        <div>
          <span>{target.label}</span>
          <strong>{target.value}</strong>
        </div>
        <div>
          <span>{plan ? '计划步骤' : '操作类型'}</span>
          <strong>{plan ? `${plan.steps.length} 个受控步骤` : action?.operationLabel || '受控企业操作'}</strong>
        </div>
        <div><span>流程状态</span><strong>{workflowStateLabel(task.workflow_state)}</strong></div>
        <div><span>流程更新序号</span><strong>{workflowRevisionLabel(task.workflow_version)}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(task.created_at)}</strong></div>
      </section>

      {plan ? (
        <section className="approval-parameters approval-plan">
          <div className="section-title">
            <div><span className="eyebrow">ACTION PLAN</span><h2>组合操作计划</h2></div>
            <span>按顺序展示审批后将执行的全部步骤</span>
          </div>
          {plan.steps.map((step) => {
            const stepPresentation = actionPresentation(step.action_type)
            const stepParameters = safeActionParameters(step.action_type, step.parameters)
            return (
              <article className="approval-plan-step" key={step.step_id}>
                <header>
                  <div>
                    <span>步骤 {step.step_order}</span>
                    <strong>{stepPresentation?.operationLabel || step.content_summary}</strong>
                  </div>
                  <Tag>{reversibilityLabel(step.reversibility)}</Tag>
                </header>
                {!stepPresentation && <p>{step.content_summary}</p>}
                <small>
                  目标对象：{actionTargetLabel(
                    step.action_type,
                    step.parameters,
                    step.target_resource,
                  )}
                  {step.depends_on_step_ids.length > 0
                    ? ` · 前置步骤：${step.depends_on_step_ids.join('、')}`
                    : ' · 无前置步骤'}
                </small>
                {stepPresentation ? (
                  <dl>
                    {stepParameters.map((field) => (
                      <div key={field.key}>
                        <dt>{field.label}</dt>
                        <dd>{safeDisplayValue(field.value)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="safe-fallback">
                    该步骤类型尚未配置安全展示模板，参数已隐藏，请联系平台管理员核对。
                  </p>
                )}
              </article>
            )
          })}
        </section>
      ) : (
        <section className="approval-parameters">
          <div className="section-title">
            <div><span className="eyebrow">ACTION PARAMETERS</span><h2>操作方案</h2></div>
            <span>执行前仍会重新校验企业最新状态</span>
          </div>
          <dl>
            {safeParameters.map((field) => (
              <div key={field.key}>
                <dt>{field.label}</dt>
                <dd>{safeDisplayValue(field.value)}</dd>
              </div>
            ))}
          </dl>
          {!action && <p className="safe-fallback">该操作类型尚未配置安全展示模板，请联系平台管理员核对。</p>}
        </section>
      )}

      {task.approval_route && <ApprovalRouteProgress route={task.approval_route} compact />}

      {task.decision ? (
        <section className={`decision-result ${task.status.toLowerCase()}`}>
          <div>
            <strong>{task.status === 'APPROVED' ? '审批已通过' : '审批已驳回'}</strong>
            <span>{formatDate(task.decision.created_at)}</span>
          </div>
          <p>{task.decision.comment || '审批人未填写备注'}</p>
          <small>处理人：{task.decision.decided_by}</small>
        </section>
      ) : (
        <footer className="approval-actions">
          <div>
            <strong>请确认业务必要性和申请范围</strong>
            <p>{task.approval_route ? '当前阶段通过后将由后端激活下一阶段；最终通过后才恢复流程执行。' : '通过后，原LangGraph流程将恢复并由Action Gateway执行最终校验。'}</p>
          </div>
          <div>
            <Button danger size="large" onClick={() => onDecision('REJECT')}>驳回</Button>
            <Button type="primary" size="large" onClick={() => onDecision('APPROVE')}>通过审批</Button>
          </div>
        </footer>
      )}

      <div className="approval-identifiers">
        <span>审批ID {task.approval_id}</span>
        {plan
          ? <span>计划ID {plan.plan_id}</span>
          : task.action_id && <span>操作ID {task.action_id}</span>}
        {task.ticket_id && <span>工单ID {task.ticket_id}</span>}
      </div>
    </div>
  )
}

export function ApprovalsPage() {
  const { approvalId } = useParams<{ approvalId: string }>()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<WorkbenchFilter>('pending')
  const [tasks, setTasks] = useState<ApprovalTask[]>([])
  const [selected, setSelected] = useState<ApprovalTask>()
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [decision, setDecision] = useState<ApprovalDecisionType>()
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const load = async () => {
      setLoadingList(true)
      try {
        const items =
          filter === 'pending' ? await listApprovals('PENDING') : await listApprovalHistory()
        setTasks(items)
        if (!approvalId && items[0]) {
          navigate(`/approvals/${items[0].approval_id}`, { replace: true })
        }
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoadingList(false)
      }
    }
    void load()
  }, [filter, navigate])

  useEffect(() => {
    if (loadingList || !approvalId) {
      setSelected(undefined)
      return
    }
    const cached = tasks.find((task) => task.approval_id === approvalId)
    if (cached) {
      setSelected(cached)
      return
    }
    const load = async () => {
      setLoadingDetail(true)
      try {
        setSelected(await getApproval(approvalId))
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoadingDetail(false)
      }
    }
    void load()
  }, [approvalId, loadingList, tasks])

  const submitDecision = async () => {
    if (!selected || !decision) return
    setSubmitting(true)
    try {
      await decideApproval({
        task: selected,
        decision,
        comment: comment.trim() || undefined,
      })
      const refreshed = await getApproval(selected.approval_id)
      setSelected(refreshed)
      setTasks((current) =>
        current.filter((task) => task.approval_id !== selected.approval_id),
      )
      setDecision(undefined)
      setComment('')
      setFilter('history')
      toast.success(decision === 'APPROVE' ? '审批已通过，流程已恢复执行' : '审批已驳回')
    } catch (error) {
      if (isRequestTimeout(error)) {
        try {
          const refreshed = await getApproval(selected.approval_id)
          if (refreshed.status !== 'PENDING') {
            setSelected(refreshed)
            setTasks((current) =>
              current.filter((task) => task.approval_id !== selected.approval_id),
            )
            setDecision(undefined)
            setComment('')
            setFilter('history')
            toast.success('审批响应等待超时，但已从服务端确认处理完成。')
            return
          }
        } catch {
          // Keep the original timeout outcome below; the backend remains authoritative.
        }
        toast.warning('审批响应等待超时，服务端可能仍在处理。请勿重复提交，稍后刷新审批历史确认。')
      } else {
        toast.error(publicErrorMessage(error))
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="approvals-page">
      <aside className="approvals-sidebar">
        <div className="panel-heading">
          <span className="eyebrow">APPROVAL WORKBENCH</span>
          <h2>审批工作台</h2>
          <p>只显示分配给当前审批人的任务</p>
        </div>
        <Segmented
          block
          value={filter}
          options={[{ label: '待我审批', value: 'pending' }, { label: '审批历史', value: 'history' }]}
          onChange={(value) => {
            setFilter(value as WorkbenchFilter)
            setSelected(undefined)
            navigate('/approvals')
          }}
        />
        <div className="approval-list">
          {loadingList ? (
            <Skeleton active paragraph={{ rows: 7 }} />
          ) : tasks.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={filter === 'pending' ? '当前没有待审批任务' : '暂无审批历史'}
            />
          ) : (
            tasks.map((task) => (
              <ApprovalListItem
                key={task.approval_id}
                task={task}
                active={task.approval_id === approvalId}
                onClick={() => navigate(`/approvals/${task.approval_id}`)}
              />
            ))
          )}
        </div>
      </aside>
      <main className="approvals-workspace">
        {loadingDetail ? (
          <div className="approval-loading"><Skeleton active paragraph={{ rows: 12 }} /></div>
        ) : selected ? (
          <ApprovalDetail task={selected} onDecision={setDecision} />
        ) : (
          <div className="approval-empty"><Empty description="选择一条审批任务查看操作方案" /></div>
        )}
      </main>

      <Modal
        title={decision === 'APPROVE' ? '确认通过审批' : '确认驳回申请'}
        open={Boolean(decision)}
        okText={decision === 'APPROVE' ? '确认通过' : '确认驳回'}
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ danger: decision === 'REJECT' }}
        onOk={() => void submitDecision()}
        onCancel={() => {
          if (!submitting) {
            setDecision(undefined)
            setComment('')
          }
        }}
      >
        <p>
          {decision === 'APPROVE'
            ? '通过后流程会从审批中断点恢复，最终操作仍需通过 Action Gateway 校验。'
            : '驳回后该流程将结束，不会执行企业写操作。'}
        </p>
        <Input.TextArea
          aria-label="审批备注"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          maxLength={1000}
          autoSize={{ minRows: 3, maxRows: 6 }}
          placeholder="填写审批依据或备注（可选）"
        />
      </Modal>
    </div>
  )
}
