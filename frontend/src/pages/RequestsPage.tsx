import { useEffect, useState } from 'react'
import { Button, Empty, Skeleton, Tag, message as toast } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { publicErrorMessage } from '../api/client'
import type { Ticket, TicketStatus, WorkflowActionPlanProgress, WorkflowBusinessSummary } from '../api/contracts'
import { getTicket, listTickets } from '../api/tickets'
import { getWorkflowProgress } from '../api/workflows'
import {
  actionPlanStatusLabel,
  actionPlanTitle,
  scenarioLabel,
  scenarioServiceLabel,
  ticketTitleLabel,
  workflowEventLabel,
  workflowStateLabel,
} from '../presentation/scenarios'

const statusView: Record<
  TicketStatus,
  { label: string; color: string; description: string }
> = {
  OPEN: { label: '待处理', color: 'default', description: '服务请求已经登记' },
  IN_PROGRESS: { label: '处理中', color: 'blue', description: '流程正在处理' },
  NEEDS_INPUT: { label: '待补充', color: 'gold', description: '需要你补充申请信息' },
  PENDING_APPROVAL: { label: '待审批', color: 'cyan', description: '正在等待审批人处理' },
  HUMAN_REVIEW: { label: '人工处理', color: 'purple', description: '已转交人工核查' },
  RESOLVED: { label: '已完成', color: 'green', description: '服务流程已经完成' },
  FAILED: { label: '处理失败', color: 'red', description: '流程未能正常完成' },
  CANCELLED: { label: '已取消', color: 'default', description: '服务请求已取消' },
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function RequestListItem({
  ticket,
  active,
  onClick,
}: {
  ticket: Ticket
  active: boolean
  onClick: () => void
}) {
  const view = statusView[ticket.status]
  return (
    <button
      type="button"
      className={`request-list-item ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      <div className="request-list-top">
        <span>{scenarioServiceLabel(ticket.scenario_key)}</span>
        <Tag color={view.color}>{view.label}</Tag>
      </div>
      <strong>{ticketTitleLabel(ticket.title)}</strong>
      <p>{view.description}</p>
      <small>{formatDate(ticket.updated_at)}</small>
    </button>
  )
}

function RequestDetail({
  ticket,
  onOpenWorkflow,
}: {
  ticket: Ticket
  onOpenWorkflow: () => void
}) {
  const current = statusView[ticket.status]
  const isEquipmentRequest = ticket.scenario_key === 'equipment_maintenance'
  const isEmployeeLifecycle = ticket.scenario_key === 'employee_lifecycle'
  const isProcurement = ticket.scenario_key === 'procurement'
  const [plan, setPlan] = useState<WorkflowActionPlanProgress | null>(null)
  const [businessSummary, setBusinessSummary] = useState<WorkflowBusinessSummary | null>(null)
  const [approvalStage, setApprovalStage] = useState<string | null>(null)
  const subjectLabel = isEquipmentRequest ? '报修设备编号' : isEmployeeLifecycle ? '目标员工' : isProcurement ? '申请物品' : '申请员工'
  const subjectValue = isEquipmentRequest
    ? ticket.subject_reference || '待补充'
    : isEmployeeLifecycle ? ticket.subject_reference || '待补充' : isProcurement ? businessSummary?.fields.item_summary || '待补充' : ticket.requester_id

  useEffect(() => {
    if (!isEmployeeLifecycle && !isProcurement) {
      setPlan(null)
      setBusinessSummary(null)
      setApprovalStage(null)
      return
    }
    let active = true
    void getWorkflowProgress(ticket.workflow_run_id)
      .then((progress) => {
        if (!active) return
        setPlan(progress.action_plan)
        setBusinessSummary(progress.business_summary || null)
        setApprovalStage(progress.approval_route?.current_stage_order
          ? `第 ${progress.approval_route.current_stage_order}/${progress.approval_route.stages.length} 级审批`
          : null)
      })
      .catch(() => { if (active) { setPlan(null); setBusinessSummary(null); setApprovalStage(null) } })
    return () => { active = false }
  }, [isEmployeeLifecycle, isProcurement, ticket.workflow_run_id])
  return (
    <div className="request-detail">
      <header className="request-detail-header">
        <div>
          <span className="eyebrow">SERVICE REQUEST</span>
          <h1>{ticketTitleLabel(ticket.title)}</h1>
          <p className="full-identifier">服务单号 {ticket.service_request_id}</p>
        </div>
        <div className="request-status-card">
          <Tag color={current.color}>{current.label}</Tag>
          <strong>{current.description}</strong>
          <small>最后更新 {formatDate(ticket.updated_at)}</small>
        </div>
      </header>

      <section className="request-facts" aria-label="服务请求信息">
        <div><span>业务场景</span><strong>{scenarioLabel(ticket.scenario_key)}</strong></div>
        <div><span>{subjectLabel}</span><strong>{subjectValue}</strong></div>
        <div><span>当前处理人</span><strong>{ticket.assignee_id || '系统自动处理'}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(ticket.created_at)}</strong></div>
        <div><span>流程状态</span><strong>{workflowStateLabel(ticket.workflow_state)}</strong></div>
        <div><span>工单编号</span><strong className="full-identifier">{ticket.ticket_id}</strong></div>
      </section>

      {isProcurement && businessSummary?.kind === 'procurement' && (
        <section className="request-plan-summary procurement-summary" aria-label="采购申请摘要">
          <div><span className="eyebrow">PROCUREMENT SUMMARY</span><h2>采购申请摘要</h2></div>
          <dl>
            <div><dt>预计申请金额</dt><dd>{businessSummary.fields.estimated_total_amount} {businessSummary.fields.currency}</dd></div>
            <div><dt>成本中心</dt><dd>{businessSummary.fields.cost_center_code}</dd></div>
            <div><dt>期望到货日期</dt><dd>{businessSummary.fields.desired_date}</dd></div>
            <div><dt>当前审批阶段</dt><dd>{approvalStage || '等待后端更新'}</dd></div>
          </dl>
          <p>金额为预计申请金额，不代表平台报价；预算与审批资格均由后端在执行前再次核验。</p>
        </section>
      )}

      {plan && (
        <section className="request-plan-summary" aria-label="操作计划进度">
          <div><span className="eyebrow">ACTION PLAN</span><h2>{actionPlanTitle(plan.plan_type)}</h2></div>
          <strong>{plan.steps.filter((step) => ['SUCCEEDED', 'REPLAYED'].includes(step.status)).length}/{plan.steps.length} 步已验证</strong>
          <p>计划状态：{actionPlanStatusLabel(plan.status)}。详细步骤和安全处置请在工作流详情中查看。</p>
        </section>
      )}

      <section className="request-timeline-section">
        <div className="section-title">
          <div>
            <span className="eyebrow">AUDIT TIMELINE</span>
            <h2>处理进度</h2>
          </div>
          <span>{ticket.events.length} 个业务事件</span>
        </div>
        <ol className="request-timeline">
          {[...ticket.events].reverse().map((event, index) => {
            const target = statusView[event.to_status]
            const reviewPayload = event.payload.workflow_payload
            const publicSummary =
              reviewPayload && typeof reviewPayload === 'object' && 'public_summary' in reviewPayload
                ? (reviewPayload as { public_summary?: unknown }).public_summary
                : null
            return (
              <li key={event.sequence} className={index === 0 ? 'latest' : ''}>
                <div className="timeline-marker"><span>{event.sequence}</span></div>
                <article>
                  <div>
                    <strong>{workflowEventLabel(event.event_type)}</strong>
                    <Tag color={target.color}>{target.label}</Tag>
                  </div>
                  <p>{typeof publicSummary === 'string' ? publicSummary : target.description}</p>
                  <small>{formatDate(event.created_at)}</small>
                </article>
              </li>
            )
          })}
        </ol>
      </section>

      <footer className="request-detail-foot">
        <span>工作流ID</span>
        <code>{ticket.workflow_run_id}</code>
        <Button type="primary" onClick={onOpenWorkflow}>查看实时执行详情</Button>
        {ticket.resolved_at && <span>完成于 {formatDate(ticket.resolved_at)}</span>}
      </footer>
    </div>
  )
}

export function RequestsPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const navigate = useNavigate()
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [selected, setSelected] = useState<Ticket>()
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    const load = async () => {
      try {
        const items = await listTickets()
        setTickets(items)
        if (!ticketId && items[0]) {
          navigate(`/requests/${items[0].ticket_id}`, { replace: true })
        }
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoadingList(false)
      }
    }
    void load()
  }, [navigate])

  useEffect(() => {
    if (loadingList || !ticketId) {
      setSelected(undefined)
      return
    }
    const cached = tickets.find((ticket) => ticket.ticket_id === ticketId)
    if (cached?.events.length) {
      setSelected(cached)
      return
    }
    const load = async () => {
      setLoadingDetail(true)
      try {
        setSelected(await getTicket(ticketId))
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoadingDetail(false)
      }
    }
    void load()
  }, [loadingList, ticketId, tickets])

  const choose = (id: string) => navigate(`/requests/${id}`)

  return (
    <div className="requests-page">
      <aside className="requests-sidebar">
        <div className="panel-heading">
          <span className="eyebrow">MY REQUESTS</span>
          <h2>我的服务请求</h2>
          <p>查看申请进度和完整业务轨迹</p>
        </div>
        <Button type="primary" block onClick={() => navigate('/service')}>
          发起新服务
        </Button>
        <div className="request-list">
          {loadingList ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : tickets.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有服务请求" />
          ) : (
            tickets.map((ticket) => (
              <RequestListItem
                key={ticket.ticket_id}
                ticket={ticket}
                active={ticket.ticket_id === ticketId}
                onClick={() => choose(ticket.ticket_id)}
              />
            ))
          )}
        </div>
      </aside>

      <main className="requests-workspace">
        {loadingDetail ? (
          <div className="request-detail-loading"><Skeleton active paragraph={{ rows: 10 }} /></div>
        ) : selected ? (
          <RequestDetail
            ticket={selected}
            onOpenWorkflow={() => navigate(`/workflows/${selected.workflow_run_id}`)}
          />
        ) : (
          <div className="request-empty-detail">
            <Empty description="选择一条服务请求查看详情" />
          </div>
        )}
      </main>
    </div>
  )
}
