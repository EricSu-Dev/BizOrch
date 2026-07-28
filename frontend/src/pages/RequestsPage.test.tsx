import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Ticket } from '../api/contracts'
import { getTicket, listTickets } from '../api/tickets'
import { getWorkflowProgress } from '../api/workflows'
import { RequestsPage } from './RequestsPage'

vi.mock('../api/tickets', () => ({
  listTickets: vi.fn(),
  getTicket: vi.fn(),
}))
vi.mock('../api/workflows', () => ({ getWorkflowProgress: vi.fn() }))

const mockedList = vi.mocked(listTickets)
const mockedGet = vi.mocked(getTicket)
const mockedProgress = vi.mocked(getWorkflowProgress)

const waitingTicket: Ticket = {
  ticket_id: 'bc59b288-1111-4222-8333-12345654dee4',
  service_request_id: '0b0f6caf-1111-4222-8333-1234569a29e1',
  workflow_run_id: 'workflow-run-1',
  requester_id: 'EMP-1001',
  assignee_id: 'EMP-MANAGER',
  scenario_key: 'access_management',
  title: 'Enterprise system access request',
  subject_reference: null,
  status: 'PENDING_APPROVAL',
  workflow_state: 'WAITING_APPROVAL',
  resolved_at: null,
  created_at: '2026-07-17T08:00:00Z',
  updated_at: '2026-07-17T08:02:00Z',
  events: [
    {
      sequence: 0,
      event_type: 'WORKFLOW_CREATED',
      from_status: null,
      to_status: 'OPEN',
      payload: {},
      created_at: '2026-07-17T08:00:00Z',
    },
    {
      sequence: 1,
      event_type: 'REQUEST_PROCESSING_STARTED',
      from_status: 'OPEN',
      to_status: 'IN_PROGRESS',
      payload: {},
      created_at: '2026-07-17T08:01:00Z',
    },
    {
      sequence: 2,
      event_type: 'APPROVAL_REQUIRED',
      from_status: 'IN_PROGRESS',
      to_status: 'PENDING_APPROVAL',
      payload: {},
      created_at: '2026-07-17T08:02:00Z',
    },
  ],
}

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/requests/:ticketId" element={<RequestsPage />} />
        <Route path="/service" element={<div>service page</div>} />
        <Route path="/workflows/:workflowRunId" element={<div>workflow page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequestsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedProgress.mockResolvedValue({
      workflow_run_id: 'workflow-run-1', ticket_id: 'ticket-1', scenario_key: 'access_management', state: 'WAITING_APPROVAL', version: 1, terminal: false,
      created_at: '2026-07-17T08:00:00Z', updated_at: '2026-07-17T08:00:00Z', events: [], action_plan: null,
    })
  })

  it('shows an owned service request and its authoritative timeline', async () => {
    mockedList.mockResolvedValue([waitingTicket])
    mockedGet.mockResolvedValue(waitingTicket)

    renderPage('/requests/bc59b288-1111-4222-8333-12345654dee4')

    expect((await screen.findAllByText('企业系统权限申请')).length).toBeGreaterThan(0)
    expect(await screen.findByText(/0b0f6caf-1111-4222-8333-1234569a29e1/)).toBeInTheDocument()
    expect(screen.getAllByText('待审批').length).toBeGreaterThan(0)
    expect(screen.queryByText('WAITING_APPROVAL')).not.toBeInTheDocument()
    expect(screen.getByText('bc59b288-1111-4222-8333-12345654dee4')).toBeInTheDocument()
    expect(screen.getByText('审批任务已创建')).toBeInTheDocument()
    expect(screen.getByText('开始校验申请信息')).toBeInTheDocument()
    expect(screen.getByText('服务流程已创建')).toBeInTheDocument()
    expect(screen.getByText('3 个业务事件')).toBeInTheDocument()
    expect(screen.getByText('EMP-MANAGER')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '查看实时执行详情' }))
    expect(await screen.findByText('workflow page')).toBeInTheDocument()
  })

  it('shows a clear empty state when the employee has no requests', async () => {
    mockedList.mockResolvedValue([])

    renderPage('/requests')

    expect(await screen.findByText('还没有服务请求')).toBeInTheDocument()
    expect(screen.getByText('选择一条服务请求查看详情')).toBeInTheDocument()
    expect(mockedGet).not.toHaveBeenCalled()
  })

  it('distinguishes an equipment maintenance request from access management', async () => {
    const equipmentTicket: Ticket = {
      ...waitingTicket,
      ticket_id: 'maintenance-ticket',
      service_request_id: 'maintenance-service',
      workflow_run_id: 'maintenance-workflow',
      scenario_key: 'equipment_maintenance',
      title: 'Industrial equipment maintenance request',
      subject_reference: 'PRESS-001',
      events: [
        {
          sequence: 1,
          event_type: 'MAINTENANCE_INFORMATION_COLLECTION_STARTED',
          from_status: 'OPEN',
          to_status: 'IN_PROGRESS',
          payload: {},
          created_at: '2026-07-19T08:51:00Z',
        },
        {
          sequence: 2,
          event_type: 'MAINTENANCE_APPROVAL_REQUIRED',
          from_status: 'IN_PROGRESS',
          to_status: 'PENDING_APPROVAL',
          payload: {},
          created_at: '2026-07-19T08:52:00Z',
        },
      ],
    }
    mockedList.mockResolvedValue([equipmentTicket])
    mockedGet.mockResolvedValue(equipmentTicket)

    renderPage('/requests/maintenance-ticket')

    expect(await screen.findByText('设备维修服务')).toBeInTheDocument()
    expect(await screen.findByText('设备维修审批任务已创建')).toBeInTheDocument()
    expect(screen.getByText('工业设备报修与维护')).toBeInTheDocument()
    expect(screen.getByText('报修设备编号')).toBeInTheDocument()
    expect(screen.getByText('PRESS-001')).toBeInTheDocument()
    expect(screen.queryByText('申请员工')).not.toBeInTheDocument()
    expect(screen.getAllByText('工业设备报修与维护申请').length).toBeGreaterThan(0)
    expect(screen.getByText('开始收集设备报修信息')).toBeInTheDocument()
    expect(screen.queryByText('MAINTENANCE_APPROVAL_REQUIRED')).not.toBeInTheDocument()
  })

  it('shows the lifecycle target employee and authoritative plan progress', async () => {
    const lifecycleTicket: Ticket = {
      ...waitingTicket,
      ticket_id: 'lifecycle-ticket',
      workflow_run_id: 'lifecycle-workflow',
      scenario_key: 'employee_lifecycle',
      title: '员工入职协同申请',
      subject_reference: 'EMP-3001',
    }
    mockedList.mockResolvedValue([lifecycleTicket])
    mockedGet.mockResolvedValue(lifecycleTicket)
    mockedProgress.mockResolvedValue({
      workflow_run_id: 'lifecycle-workflow',
      ticket_id: 'lifecycle-ticket',
      scenario_key: 'employee_lifecycle',
      state: 'EXECUTING',
      version: 3,
      terminal: false,
      created_at: '2026-07-23T08:00:00Z',
      updated_at: '2026-07-23T08:01:00Z',
      events: [],
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
            action_type: 'create_pending_employee', content_summary: '创建档案',
            reversibility: 'MANUAL_ONLY', status: 'SUCCEEDED', attempt_count: 1,
            last_error_code: null, started_at: null, completed_at: null,
          },
          {
            step_id: 'step-2', step_order: 2, depends_on_step_ids: ['step-1'],
            action_type: 'create_disabled_corporate_account', content_summary: '创建账号',
            reversibility: 'REVERSIBLE', status: 'PENDING', attempt_count: 0,
            last_error_code: null, started_at: null, completed_at: null,
          },
        ],
      },
    })

    renderPage('/requests/lifecycle-ticket')

    expect(await screen.findByText('目标员工')).toBeInTheDocument()
    expect(screen.getByText('EMP-3001')).toBeInTheDocument()
    expect(await screen.findByText('员工入职操作计划')).toBeInTheDocument()
    expect(screen.getByText('1/2 步已验证')).toBeInTheDocument()
    expect(screen.getByText(/计划状态：执行中/)).toBeInTheDocument()
  })
})
