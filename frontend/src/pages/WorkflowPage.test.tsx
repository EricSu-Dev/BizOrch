import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowProgress } from '../api/contracts'
import {
  getWorkflowProgress,
  subscribeWorkflowProgress,
} from '../api/workflows'
import { WorkflowPage } from './WorkflowPage'

vi.mock('../api/workflows', () => ({
  getWorkflowProgress: vi.fn(),
  subscribeWorkflowProgress: vi.fn(),
}))

const mockedGet = vi.mocked(getWorkflowProgress)
const mockedSubscribe = vi.mocked(subscribeWorkflowProgress)

const waitingProgress: WorkflowProgress = {
  workflow_run_id: 'workflow-run-1',
  ticket_id: 'ticket-1',
  scenario_key: 'access_management',
  state: 'WAITING_APPROVAL',
  version: 2,
  terminal: false,
  created_at: '2026-07-17T08:00:00Z',
  updated_at: '2026-07-17T08:02:00Z',
  action_plan: null,
  events: [
    {
      sequence: 0,
      event_type: 'WORKFLOW_CREATED',
      from_state: null,
      to_state: 'CREATED',
      created_at: '2026-07-17T08:00:00Z',
    },
    {
      sequence: 2,
      event_type: 'APPROVAL_REQUIRED',
      from_state: 'RUNNING',
      to_state: 'WAITING_APPROVAL',
      created_at: '2026-07-17T08:02:00Z',
    },
  ],
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/workflows/workflow-run-1']}>
      <Routes>
        <Route path="/workflows/:workflowRunId" element={<WorkflowPage />} />
        <Route path="/requests/:ticketId" element={<div>request page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('WorkflowPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows safe progress and applies a terminal SSE snapshot', async () => {
    let emit: ((snapshot: WorkflowProgress) => void) | undefined
    mockedGet.mockResolvedValue(waitingProgress)
    mockedSubscribe.mockImplementation((_id, onSnapshot, onOpen) => {
      emit = onSnapshot
      onOpen()
      return vi.fn()
    })

    renderPage()

    expect(await screen.findByText('工作流执行详情')).toBeInTheDocument()
    expect(screen.getByText('实时连接')).toBeInTheDocument()
    expect(screen.getByText('审批任务已创建')).toBeInTheDocument()
    expect(screen.getByText('处理中 → 待审批')).toBeInTheDocument()
    expect(screen.getByText('2 个权威事件')).toBeInTheDocument()

    const completed: WorkflowProgress = {
      ...waitingProgress,
      state: 'COMPLETED',
      version: 4,
      terminal: true,
      updated_at: '2026-07-17T08:04:00Z',
      events: [
        ...waitingProgress.events,
        {
          sequence: 4,
          event_type: 'ACTION_SUCCEEDED',
          from_state: 'EXECUTING',
          to_state: 'COMPLETED',
          created_at: '2026-07-17T08:04:00Z',
        },
      ],
    }
    act(() => emit?.(completed))

    expect(screen.getByText('流程已结束')).toBeInTheDocument()
    expect(screen.getByText('授权执行并验证成功')).toBeInTheDocument()
    expect(screen.getByText('3 个权威事件')).toBeInTheDocument()
    expect(screen.getAllByText('V4').length).toBeGreaterThan(0)
  })

  it('shows the Chinese maintenance scenario label without exposing the raw key', async () => {
    mockedGet.mockResolvedValue({
      ...waitingProgress,
      scenario_key: 'equipment_maintenance',
      events: [
        {
          sequence: 1,
          event_type: 'MAINTENANCE_INFORMATION_COLLECTION_STARTED',
          from_state: 'CREATED',
          to_state: 'RUNNING',
          created_at: '2026-07-19T08:51:00Z',
        },
        {
          sequence: 2,
          event_type: 'MAINTENANCE_APPROVAL_REQUIRED',
          from_state: 'RUNNING',
          to_state: 'WAITING_APPROVAL',
          created_at: '2026-07-19T08:52:00Z',
        },
      ],
    })
    mockedSubscribe.mockImplementation((_id, _onSnapshot, onOpen) => {
      onOpen()
      return vi.fn()
    })

    renderPage()

    expect(await screen.findByText('工业设备报修与维护')).toBeInTheDocument()
    expect(screen.getByText('设备维修审批任务已创建')).toBeInTheDocument()
    expect(screen.getByText('开始收集设备报修信息')).toBeInTheDocument()
    expect(screen.queryByText('MAINTENANCE_APPROVAL_REQUIRED')).not.toBeInTheDocument()
    expect(screen.queryByText('equipment_maintenance')).not.toBeInTheDocument()
  })

  it('renders only safe lifecycle plan progress with Chinese step states', async () => {
    mockedGet.mockResolvedValue({
      ...waitingProgress,
      scenario_key: 'employee_lifecycle',
      action_plan: {
        plan_id: 'plan-1',
        plan_version: 1,
        plan_type: 'ONBOARDING',
        subject_reference: 'EMP-3001',
        content_summary: '为员工 EMP-3001 办理入职协同',
        status: 'EXECUTING',
        steps: [
          {
            step_id: 'step-1', step_order: 1, depends_on_step_ids: [],
            action_type: 'create_pending_employee', content_summary: '创建待入职员工档案',
            reversibility: 'MANUAL_ONLY', status: 'SUCCEEDED', attempt_count: 1,
            last_error_code: null, started_at: '2026-07-23T08:00:00Z', completed_at: '2026-07-23T08:00:02Z',
          },
          {
            step_id: 'step-2', step_order: 2, depends_on_step_ids: ['step-1'],
            action_type: 'create_disabled_corporate_account', content_summary: '创建停用状态企业账号',
            reversibility: 'REVERSIBLE', status: 'PENDING', attempt_count: 0,
            last_error_code: null, started_at: null, completed_at: null,
          },
        ],
      },
    })
    mockedSubscribe.mockImplementation((_id, _onSnapshot, onOpen) => {
      onOpen()
      return vi.fn()
    })

    renderPage()

    expect(await screen.findByRole('heading', { name: '员工入职操作计划' })).toBeInTheDocument()
    expect(screen.getByText('目标员工：EMP-3001 · 已验证 1/2 步')).toBeInTheDocument()
    expect(screen.getAllByText('创建待入职员工档案')).toHaveLength(2)
    expect(screen.getByText('已验证成功')).toBeInTheDocument()
    expect(screen.getByText('待执行')).toBeInTheDocument()
    expect(screen.queryByText('content_digest')).not.toBeInTheDocument()
  })

  it('renders a procurement route and never exposes a route digest', async () => {
    mockedGet.mockResolvedValue({
      ...waitingProgress,
      scenario_key: 'procurement',
      approval_route: {
        sequence_id: 'sequence-1', route_version: 3, status: 'PENDING', current_stage_order: 2,
        completed_at: null,
        stages: [
          { approval_id: 'approval-1', stage_order: 1, stage_code: 'BUSINESS_CONFIRMATION', approver_id: 'EMP-MANAGER', status: 'APPROVED', decided_at: '2026-07-26T08:00:00Z' },
          { approval_id: 'approval-2', stage_order: 2, stage_code: 'BUDGET_CONFIRMATION', approver_id: 'EMP-BUDGET', status: 'PENDING', decided_at: null },
          { approval_id: 'approval-3', stage_order: 3, stage_code: 'PROCUREMENT_CONFIRMATION', approver_id: 'EMP-PROCUREMENT', status: 'QUEUED', decided_at: null },
        ],
      },
      business_summary: {
        kind: 'procurement',
        fields: { item_summary: 'A4打印纸 × 2', estimated_total_amount: '260.00', currency: 'CNY', cost_center_code: 'CC-ADMIN', desired_date: '2026-08-05', business_reason: '补充办公区用品' },
      },
    })
    mockedSubscribe.mockImplementation((_id, _onSnapshot, onOpen) => {
      onOpen()
      return vi.fn()
    })

    renderPage()

    expect(await screen.findByText('采购与办公申请')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '审批路线' })).toBeInTheDocument()
    expect(screen.getByText('业务确认')).toBeInTheDocument()
    expect(screen.getByText('预算确认')).toBeInTheDocument()
    expect(screen.getByText('采购复核')).toBeInTheDocument()
    expect(screen.getByText('第 2/3 级')).toBeInTheDocument()
    expect(screen.getByText('审批路线编号：sequence-1')).toBeInTheDocument()
    expect(screen.queryByText(/digest/i)).not.toBeInTheDocument()
  })
})
