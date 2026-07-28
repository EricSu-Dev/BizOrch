import { AxiosError } from 'axios'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  decideApproval,
  getApproval,
  listApprovalHistory,
  listApprovals,
} from '../api/approvals'
import type { ApprovalTask } from '../api/contracts'
import { ApprovalsPage } from './ApprovalsPage'

vi.mock('../api/approvals', () => ({
  listApprovals: vi.fn(),
  listApprovalHistory: vi.fn(),
  getApproval: vi.fn(),
  decideApproval: vi.fn(),
}))

const mockedList = vi.mocked(listApprovals)
const mockedHistory = vi.mocked(listApprovalHistory)
const mockedGet = vi.mocked(getApproval)
const mockedDecide = vi.mocked(decideApproval)

const pendingTask: ApprovalTask = {
  approval_id: 'approval-1',
  workflow_run_id: 'workflow-1',
  workflow_state: 'WAITING_APPROVAL',
  workflow_version: 4,
  ticket_id: 'ticket-1',
  requester_id: 'EMP-1001',
  approval_subject_type: 'ACTION',
  action_id: 'action-1',
  action_version: 1,
  action_type: 'grant_application_access',
  target_resource: 'application/CRM/employee/EMP-1001',
  parameters: {
    employee_id: 'EMP-1001',
    application_code: 'CRM',
    role_code: 'read_only',
    duration_days: 30,
  },
  content_summary: 'Grant CRM read_only access for 30 days',
  action_plan: null,
  status: 'PENDING',
  created_at: '2026-07-17T08:00:00Z',
  decided_at: null,
  decision: null,
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/approvals/approval-1']}>
      <Routes>
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/:approvalId" element={<ApprovalsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ApprovalsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedList.mockResolvedValue([pendingTask])
    mockedHistory.mockResolvedValue([])
  })

  it('shows the exact versioned action proposal assigned to the approver', async () => {
    renderPage()

    expect(await screen.findByText((_, element) =>
      element?.tagName === 'STRONG' &&
      element.textContent === '审批绑定操作方案第 1 版'
    )).toBeInTheDocument()
    expect(screen.getAllByText('为 EMP-1001 授予 CRM 系统 · 只读权限 · 30 天')).toHaveLength(2)
    expect(screen.getByText('待审批', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.queryByText('WAITING_APPROVAL')).not.toBeInTheDocument()
    expect(screen.getByText('授权范围')).toBeInTheDocument()
    expect(screen.getAllByText('CRM 系统 · 只读权限 · 30 天')).toHaveLength(1)
    expect(screen.getByText('read_only')).toBeInTheDocument()
    expect(screen.getByText('第 4 次状态更新')).toBeInTheDocument()
  })

  it('submits the displayed workflow and action version when approving', async () => {
    const approvedTask: ApprovalTask = {
      ...pendingTask,
      status: 'APPROVED',
      workflow_state: 'COMPLETED',
      workflow_version: 7,
      decided_at: '2026-07-17T08:05:00Z',
      decision: {
        decision: 'APPROVE',
        decided_by: 'EMP-MANAGER',
        comment: 'Confirmed business need',
        created_at: '2026-07-17T08:05:00Z',
      },
    }
    mockedDecide.mockResolvedValue({
      approval_id: 'approval-1',
      approval_status: 'APPROVED',
      scenario_key: 'access_management',
      workflow_run_id: 'workflow-1',
      workflow_state: 'COMPLETED',
      workflow_version: 7,
      checkpoint_pending: false,
    })
    mockedGet.mockResolvedValue(approvedTask)
    mockedHistory.mockResolvedValue([approvedTask])
    renderPage()

    fireEvent.click(
      await screen.findByRole('button', { name: /通\s*过\s*审\s*批/ }),
    )
    fireEvent.change(screen.getByLabelText('审批备注'), {
      target: { value: 'Confirmed business need' },
    })
    fireEvent.click(screen.getByRole('button', { name: /确\s*认\s*通\s*过/ }))

    await waitFor(() => {
      expect(mockedDecide).toHaveBeenCalledWith({
        task: pendingTask,
        decision: 'APPROVE',
        comment: 'Confirmed business need',
      })
    })
    const resultHeading = await screen.findByText('审批已通过')
    const resultSection = resultHeading.closest('section')
    expect(resultSection).toHaveTextContent('Confirmed business need')
    expect(resultSection).toHaveTextContent('处理人：EMP-MANAGER')
  })

  it('reconciles a timed-out approval response with the authoritative detail', async () => {
    const approvedTask: ApprovalTask = {
      ...pendingTask,
      status: 'APPROVED',
      workflow_state: 'COMPLETED',
      workflow_version: 7,
      decided_at: '2026-07-17T08:05:00Z',
      decision: {
        decision: 'APPROVE',
        decided_by: 'EMP-MANAGER',
        comment: null,
        created_at: '2026-07-17T08:05:00Z',
      },
    }
    mockedDecide.mockRejectedValue(new AxiosError('timeout', 'ECONNABORTED'))
    mockedGet.mockResolvedValue(approvedTask)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: /通\s*过\s*审\s*批/ }))
    fireEvent.click(screen.getByRole('button', { name: /确\s*认\s*通\s*过/ }))

    expect(await screen.findByText('审批已通过')).toBeInTheDocument()
    expect(mockedGet).toHaveBeenCalledWith('approval-1')
  })

  it('renders a maintenance work-order proposal with a strict parameter whitelist', async () => {
    const maintenanceTask: ApprovalTask = {
      ...pendingTask,
      action_type: 'create_maintenance_work_order',
      target_resource: 'equipment/EQ-1001/maintenance-work-orders',
      content_summary: '为设备 EQ-1001 创建 HIGH 优先级维修工单',
      parameters: {
        requester_id: 'EMP-1001',
        equipment_code: 'EQ-1001',
        expected_equipment_version: 7,
        fault_description: '异常振动',
        observed_at: '2026-07-19T10:30:00+08:00',
        production_impact: 'SLOWDOWN',
        safety_observation: '暂无明显安全风险',
        business_reason: '恢复生产稳定性',
        priority: 'MEDIUM',
        secret_token: 'must-not-render',
      },
    }
    mockedList.mockResolvedValue([maintenanceTask])

    renderPage()

    expect(await screen.findByRole('heading', { name: '设备维修工单审批' })).toBeInTheDocument()
    expect(screen.getByText('创建设备维修工单')).toBeInTheDocument()
    expect(screen.getByText('故障现象')).toBeInTheDocument()
    expect(screen.getByText('异常振动')).toBeInTheDocument()
    expect(screen.queryByText('secret_token')).not.toBeInTheDocument()
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
  })

  it('uses a safe fallback for an unknown action without dumping parameters', async () => {
    mockedList.mockResolvedValue([{
      ...pendingTask,
      action_type: 'future_sensitive_action',
      parameters: { password: 'never-render-this' },
    }])

    renderPage()

    expect(await screen.findByRole('heading', { name: '企业操作审批' })).toBeInTheDocument()
    expect(screen.getByText(/尚未配置安全展示模板/)).toBeInTheDocument()
    expect(screen.queryByText('never-render-this')).not.toBeInTheDocument()
  })

  it('renders an exact versioned composite plan without dumping unknown step parameters', async () => {
    const planTask: ApprovalTask = {
      ...pendingTask,
      approval_subject_type: 'PLAN',
      action_id: null,
      action_version: null,
      action_type: null,
      target_resource: null,
      parameters: null,
      content_summary: '为 EMP-3001 办理入职',
      action_plan: {
        plan_id: 'plan-1',
        plan_version: 2,
        scenario_key: 'employee_lifecycle',
        plan_type: 'ONBOARDING',
        subject_reference: 'EMP-3001',
        content_summary: '为 EMP-3001 办理入职',
        steps: [
          {
            step_id: 'step-1',
            step_order: 1,
            depends_on_step_ids: [],
            action_id: 'action-create-employee',
            action_version: 2,
            action_type: 'create_pending_employee',
            target_resource: 'employee/EMP-3001',
            parameters: {
              subject_employee_id: 'EMP-3001',
              temporary_password: 'must-not-render',
            },
            content_summary: '创建待入职员工档案',
            reversibility: 'MANUAL_ONLY',
            compensation_action_type: null,
          },
          {
            step_id: 'step-2',
            step_order: 2,
            depends_on_step_ids: ['step-1'],
            action_id: 'action-create-account',
            action_version: 2,
            action_type: 'create_disabled_corporate_account',
            target_resource: 'account/EMP-3001',
            parameters: { subject_employee_id: 'EMP-3001' },
            content_summary: '创建停用状态企业账号',
            reversibility: 'REVERSIBLE',
            compensation_action_type: 'remove_disabled_corporate_account',
          },
        ],
      },
    }
    mockedList.mockResolvedValue([planTask])

    renderPage()

    expect(await screen.findByRole('heading', { name: '员工入职操作计划审批' })).toBeInTheDocument()
    expect(screen.getByText('审批绑定组合操作计划第 2 版')).toBeInTheDocument()
    expect(screen.getByText('2 个受控步骤')).toBeInTheDocument()
    expect(screen.getByText('步骤 1')).toBeInTheDocument()
    expect(screen.getByText('步骤 2')).toBeInTheDocument()
    expect(screen.getByText(/前置步骤：step-1/)).toBeInTheDocument()
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
    expect(screen.getByText('计划ID plan-1')).toBeInTheDocument()
  })

  it('shows the active procurement responsibility and the full serial route safely', async () => {
    const procurementTask: ApprovalTask = {
      ...pendingTask,
      action_type: 'CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET',
      target_resource: 'cost-center/CC-ADMIN',
      content_summary: '办公采购：1项，预计260.00元，成本中心CC-ADMIN',
      parameters: {
        items: [{ item_name: 'A4打印纸', quantity: 2, secret_note: 'must-not-render' }],
        estimated_total_amount: '260.00', currency: 'CNY', cost_center_code: 'CC-ADMIN',
        desired_date: '2026-08-05', delivery_location_code: '上海总部', business_reason_summary: '补充新员工办公区用品',
      },
      approval_sequence_id: 'sequence-1', stage_order: 2, stage_code: 'BUDGET_CONFIRMATION',
      approval_route: {
        sequence_id: 'sequence-1', route_version: 3, status: 'PENDING', current_stage_order: 2, completed_at: null,
        stages: [
          { approval_id: 'approval-1', stage_order: 1, stage_code: 'BUSINESS_CONFIRMATION', approver_id: 'EMP-MANAGER', status: 'APPROVED', decided_at: '2026-07-26T08:00:00Z' },
          { approval_id: 'approval-2', stage_order: 2, stage_code: 'BUDGET_CONFIRMATION', approver_id: 'EMP-BUDGET', status: 'PENDING', decided_at: null },
          { approval_id: 'approval-3', stage_order: 3, stage_code: 'PROCUREMENT_CONFIRMATION', approver_id: 'EMP-PROCUREMENT', status: 'QUEUED', decided_at: null },
        ],
      },
    }
    mockedList.mockResolvedValue([procurementTask])

    renderPage()

    expect(await screen.findByText('当前审批职责：预算确认')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '审批路线' })).toBeInTheDocument()
    expect(screen.getByText('待后续处理')).toBeInTheDocument()
    expect(screen.getByText('A4打印纸 × 2')).toBeInTheDocument()
    expect(screen.getByText('预算成本中心')).toBeInTheDocument()
    expect(screen.getAllByText('成本中心 CC-ADMIN')).toHaveLength(1)
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
    expect(screen.queryByText(/digest/i)).not.toBeInTheDocument()
  })
})
