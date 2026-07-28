import { useEffect, useState } from 'react'
import { Button, Empty, Skeleton, Tag, message as toast } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { ApprovalRouteProgress } from '../components/ApprovalRouteProgress'
import { publicErrorMessage } from '../api/client'
import type {
  WorkflowActionPlanProgress,
  WorkflowProgress,
  WorkflowState,
} from '../api/contracts'
import {
  getWorkflowProgress,
  subscribeWorkflowProgress,
} from '../api/workflows'
import {
  actionPlanStatusLabel,
  actionPlanStepStatusColor,
  actionPlanStepStatusLabel,
  actionPlanTitle,
  actionPresentation,
  scenarioLabel,
  workflowEventLabel,
  workflowStateLabel,
} from '../presentation/scenarios'

type ConnectionState = 'connecting' | 'live' | 'closed' | 'error'

const stateView: Record<
  WorkflowState,
  { label: string; color: string; description: string }
> = {
  CREATED: { label: '已创建', color: 'default', description: '流程已经登记' },
  RUNNING: { label: '处理中', color: 'blue', description: '正在执行规则与业务处理' },
  WAITING_USER: { label: '待补充', color: 'gold', description: '等待申请人补充信息' },
  WAITING_APPROVAL: { label: '待审批', color: 'cyan', description: '等待指定审批人决定' },
  WAITING_HUMAN: { label: '人工处理', color: 'purple', description: '自动流程已安全暂停' },
  EXECUTING: { label: '执行中', color: 'geekblue', description: '正在执行受控企业操作' },
  COMPLETED: { label: '已完成', color: 'green', description: '流程已经正常结束' },
  FAILED: { label: '失败', color: 'red', description: '流程执行失败' },
  CANCELLED: { label: '已取消', color: 'default', description: '流程已经取消' },
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function PlanProgress({ plan }: { plan: WorkflowActionPlanProgress }) {
  const completed = plan.steps.filter((step) =>
    ['SUCCEEDED', 'REPLAYED'].includes(step.status),
  ).length
  const subjectLabel = plan.plan_type === 'OFFICE_PROCUREMENT'
    ? '成本中心'
    : '目标员工'
  return (
    <section className="workflow-plan-section">
      <div className="section-title">
        <div><span className="eyebrow">ACTION PLAN</span><h2>{actionPlanTitle(plan.plan_type)}</h2></div>
        <Tag color={actionPlanStepStatusColor(plan.status)}>{actionPlanStatusLabel(plan.status)}</Tag>
      </div>
      <p className="workflow-plan-summary">{subjectLabel}：{plan.subject_reference} · 已验证 {completed}/{plan.steps.length} 步</p>
      <ol className="workflow-plan-steps">
        {plan.steps.map((step) => {
          const presentation = actionPresentation(step.action_type)
          return (
            <li key={step.step_id}>
              <div className="workflow-plan-step-number">{step.step_order}</div>
              <article>
                <div>
                  <strong>{presentation?.operationLabel || step.content_summary}</strong>
                  <Tag color={actionPlanStepStatusColor(step.status)}>{actionPlanStepStatusLabel(step.status)}</Tag>
                </div>
                <p>{step.content_summary}</p>
                <small>
                  {step.depends_on_step_ids.length ? '依赖前置步骤完成' : '起始步骤'}
                  {step.attempt_count > 0 ? ` · 已尝试 ${step.attempt_count} 次` : ''}
                  {step.completed_at ? ` · 完成于 ${formatDate(step.completed_at)}` : ''}
                </small>
                {step.last_error_code && <p className="safe-fallback">该步骤未自动继续，等待安全处置。</p>}
              </article>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

export function WorkflowPage() {
  const { workflowRunId } = useParams<{ workflowRunId: string }>()
  const navigate = useNavigate()
  const [progress, setProgress] = useState<WorkflowProgress>()
  const [loading, setLoading] = useState(true)
  const [connection, setConnection] = useState<ConnectionState>('connecting')

  useEffect(() => {
    if (!workflowRunId) return
    const load = async () => {
      try {
        const snapshot = await getWorkflowProgress(workflowRunId)
        setProgress(snapshot)
        if (snapshot.terminal) setConnection('closed')
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [workflowRunId])

  useEffect(() => {
    if (!workflowRunId) return
    setConnection('connecting')
    return subscribeWorkflowProgress(
      workflowRunId,
      (snapshot) => {
        setProgress(snapshot)
        setConnection(snapshot.terminal ? 'closed' : 'live')
      },
      () => setConnection('live'),
      () => setConnection('error'),
    )
  }, [workflowRunId])

  if (loading) {
    return <div className="workflow-page-loading"><Skeleton active paragraph={{ rows: 12 }} /></div>
  }
  if (!progress) {
    return <div className="workflow-page-empty"><Empty description="没有找到可查看的工作流" /></div>
  }

  const current = stateView[progress.state]
  const connectionView = {
    connecting: { label: '正在连接', color: 'processing' },
    live: { label: '实时连接', color: 'success' },
    closed: { label: '流程已结束', color: 'default' },
    error: { label: '实时连接中断', color: 'warning' },
  }[connection]

  return (
    <div className="workflow-page">
      <header className="workflow-hero">
        <div>
          <Button type="link" onClick={() => navigate(`/requests/${progress.ticket_id}`)}>
            ← 返回服务请求
          </Button>
          <span className="eyebrow">LIVE WORKFLOW</span>
          <h1>工作流执行详情</h1>
          <p>这里展示可审计的业务状态，不展示模型隐藏推理或内部敏感参数。</p>
        </div>
        <div className="workflow-current-card">
          <div><Tag color={current.color}>{current.label}</Tag><Tag color={connectionView.color}>{connectionView.label}</Tag></div>
          <strong>{current.description}</strong>
          <span>当前版本 V{progress.version}</span>
        </div>
      </header>

      <section className="workflow-facts" aria-label="工作流信息">
        <div><span>业务场景</span><strong>{scenarioLabel(progress.scenario_key)}</strong></div>
        <div><span>当前状态</span><strong>{workflowStateLabel(progress.state)}</strong></div>
        <div><span>流程版本</span><strong>V{progress.version}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(progress.created_at)}</strong></div>
        <div><span>最后更新</span><strong>{formatDate(progress.updated_at)}</strong></div>
        <div><span>流程ID</span><strong title={progress.workflow_run_id}>{progress.workflow_run_id}</strong></div>
      </section>

      {progress.action_plan && <PlanProgress plan={progress.action_plan} />}

      {progress.approval_route && <ApprovalRouteProgress route={progress.approval_route} />}

      <section className="workflow-event-section">
        <div className="section-title">
          <div><span className="eyebrow">AUTHORITATIVE EVENTS</span><h2>执行轨迹</h2></div>
          <span>{progress.events.length} 个权威事件</span>
        </div>
        <ol className="workflow-event-list">
          {[...progress.events].reverse().map((event, index) => {
            const target = stateView[event.to_state]
            return (
              <li key={event.sequence} className={index === 0 ? 'latest' : ''}>
                <div className="workflow-event-sequence">{event.sequence}</div>
                <article>
                  <div><strong>{workflowEventLabel(event.event_type)}</strong><Tag color={target.color}>{target.label}</Tag></div>
                  <p>{event.from_state ? `${workflowStateLabel(event.from_state)} → ${workflowStateLabel(event.to_state)}` : workflowStateLabel(event.to_state)}</p>
                  <small>{formatDate(event.created_at)}</small>
                </article>
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}
