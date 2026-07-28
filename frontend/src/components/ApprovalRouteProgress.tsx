import { Tag } from 'antd'

import type { ApprovalRouteProgress } from '../api/contracts'
import {
  approvalStageLabel,
  approvalStageStatusColor,
  approvalStageStatusLabel,
} from '../presentation/scenarios'

function formatDate(value: string | null): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '—'
}

export function ApprovalRouteProgress({
  route,
  compact = false,
}: {
  route: ApprovalRouteProgress
  compact?: boolean
}) {
  return (
    <section className={`approval-route-progress ${compact ? 'compact' : ''}`} aria-label="审批路线">
      <div className="section-title">
        <div>
          <span className="eyebrow">APPROVAL ROUTE</span>
          <h2>审批路线</h2>
        </div>
        <span>
          {route.current_stage_order
            ? `第 ${route.current_stage_order}/${route.stages.length} 级`
            : `${route.stages.length} 级已结束`}
        </span>
      </div>
      <p className="approval-route-note">路线版本 V{route.route_version}，每次决定均绑定当前计划与路线版本。</p>
      <ol className="approval-route-stages">
        {route.stages.map((stage) => (
          <li key={stage.approval_id}>
            <div className="approval-route-order">{stage.stage_order}</div>
            <div>
              <strong>{approvalStageLabel(stage.stage_code)}</strong>
              <span>审批人：{stage.approver_id}</span>
              {stage.decided_at && <small>决定时间：{formatDate(stage.decided_at)}</small>}
            </div>
            <Tag color={approvalStageStatusColor(stage.status)}>
              {approvalStageStatusLabel(stage.status)}
            </Tag>
          </li>
        ))}
      </ol>
      {!compact && <p className="approval-route-id">审批路线编号：{route.sequence_id}</p>}
    </section>
  )
}
