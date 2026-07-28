import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteConversation,
  listConversations,
  listMessages,
  renameConversation,
  sendAgentMessage,
} from '../api/conversations'
import { ChatPage } from './ChatPage'

vi.mock('../api/conversations', () => ({
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  renameConversation: vi.fn(),
  sendAgentMessage: vi.fn(),
}))

const mockedListConversations = vi.mocked(listConversations)
const mockedListMessages = vi.mocked(listMessages)
const mockedSend = vi.mocked(sendAgentMessage)
const mockedRename = vi.mocked(renameConversation)
const mockedDelete = vi.mocked(deleteConversation)

describe('ChatPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.resetAllMocks()
  })

  it('loads persisted conversation messages and safe workflow details', async () => {
    mockedListConversations.mockResolvedValue([
      {
        conversation_id: 'conversation-1',
        title: null,
        scenario_key: 'access_management',
        workflow_run_id: 'workflow-1',
        status: 'OPEN',
        message_count: 2,
        created_at: '2026-07-17T08:00:00Z',
        updated_at: '2026-07-17T08:01:00Z',
      },
    ])
    mockedListMessages.mockResolvedValue([
      {
        message_id: 'message-1',
        sequence: 1,
        role: 'USER',
        content: '申请CRM权限',
        client_message_id: 'client-1',
        agent_result: null,
        created_at: '2026-07-17T08:00:00Z',
      },
      {
        message_id: 'message-2',
        sequence: 2,
        role: 'ASSISTANT',
        content: '请补充使用期限。',
        client_message_id: null,
        created_at: '2026-07-17T08:01:00Z',
        agent_result: {
          request_id: 'request-1',
          intent: 'ACCESS_REQUEST',
          reply: '请补充使用期限。',
          scenario_key: 'access_management',
          knowledge: {
            citations: [
              {
                document_id: 'document-1',
                chunk_id: 'chunk-1',
                chunk_index: 0,
                title: '临时系统权限申请与审批指南',
                source_uri: 'knowledge://temporary-access-guide',
                version_label: '2026.1',
                source_department: '企业IT服务部',
                trust_level: 'AUTHORITATIVE',
                excerpt: '申请人应明确目标系统、角色、使用期限和可核验的业务理由。审批人应检查最小权限原则，批准后仍需在执行前复核最新企业状态。',
                vector_score: 0.9,
                lexical_score: 0.96,
                combined_score: 0.92,
              },
            ],
          },
          scenario_summary: {
            manager_id: 'EMP-MANAGER',
          },
          workflow: {
            scenario_key: 'access_management',
            workflow_run_id: 'workflow-1',
            service_request_id: 'service-1',
            ticket_id: 'ticket-1',
            workflow_state: 'WAITING_USER',
            workflow_version: 2,
            approval_id: null,
            missing_fields: ['duration_days'],
          },
          trace: [
            {
              agent_name: 'supervisor',
              capability: 'intent_and_field_extraction',
              status: 'SUCCEEDED',
              summary: 'routed intent ACCESS_REQUEST',
            },
          ],
        },
      },
    ])

    render(<ChatPage />)

    expect(await screen.findByText('申请CRM权限')).toBeInTheDocument()
    expect(screen.getByText('等待补充信息')).toBeInTheDocument()
    expect(screen.getByText('待补充：使用期限')).toBeInTheDocument()
    expect(screen.getByText('已关联业务流程')).toBeInTheDocument()
    expect(screen.getByText('任务理解智能体')).toBeInTheDocument()
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText('意图识别与字段提取：已识别为“企业系统权限申请”')).toBeInTheDocument()

    const excerpt = screen.getByText(/申请人应明确目标系统/)
    expect(excerpt).toHaveClass('collapsed')
    fireEvent.click(screen.getByRole('button', { name: '展开完整引用' }))
    expect(excerpt).toHaveClass('expanded')
    expect(screen.getByRole('button', { name: '收起完整引用' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('sends one client-idempotent message and reloads persisted history', async () => {
    mockedListConversations
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          conversation_id: 'conversation-1',
          title: null,
          scenario_key: 'access_management',
          workflow_run_id: 'workflow-1',
          status: 'OPEN',
          message_count: 2,
          created_at: '2026-07-17T08:00:00Z',
          updated_at: '2026-07-17T08:01:00Z',
        },
      ])
    mockedSend.mockResolvedValue({
      conversation_id: 'conversation-1',
      client_message_id: 'client-fixed',
      agent: {
        request_id: 'request-1',
        intent: 'UNKNOWN',
        reply: '已受理',
        scenario_key: null,
        knowledge: null,
        scenario_summary: null,
        workflow: null,
        trace: [],
      },
    })
    mockedListMessages.mockResolvedValue([
      {
        message_id: 'server-user',
        sequence: 1,
        role: 'USER',
        content: '申请CRM只读权限30天，用于客户项目支持。',
        client_message_id: 'client-fixed',
        agent_result: null,
        created_at: '2026-07-17T08:00:00Z',
      },
      {
        message_id: 'server-assistant',
        sequence: 2,
        role: 'ASSISTANT',
        content: '已受理',
        client_message_id: null,
        agent_result: null,
        created_at: '2026-07-17T08:01:00Z',
      },
    ])
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(
      '00000000-0000-4000-8000-000000000001',
    )

    render(<ChatPage />)
    fireEvent.click(await screen.findByRole('button', { name: '申请系统权限' }))
    fireEvent.click(screen.getByRole('button', { name: /发\s*送/ }))

    await waitFor(() => {
      expect(mockedSend).toHaveBeenCalledWith({
        message: '申请CRM只读权限30天，用于客户项目支持。',
        clientMessageId: '00000000-0000-4000-8000-000000000001',
        conversationId: undefined,
      })
    })
    expect(await screen.findByText('已受理')).toBeInTheDocument()
  })

  it('uses an existing demo equipment code in the maintenance quick prompt', async () => {
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])

    render(<ChatPage />)

    fireEvent.click(await screen.findByRole('button', { name: '发起设备报修' }))
    const prompt = (screen.getByRole('textbox') as HTMLTextAreaElement).value
    expect(prompt).toContain('PRESS-001')
    expect(prompt).not.toContain('EQ-1001')
  })

  it('shows only the whitelisted equipment summary with Chinese labels', async () => {
    mockedListConversations.mockResolvedValue([{
      conversation_id: 'maintenance-conversation',
      title: null,
      scenario_key: 'equipment_maintenance',
      workflow_run_id: 'maintenance-workflow',
      status: 'OPEN',
      message_count: 1,
      created_at: '2026-07-19T08:00:00Z',
      updated_at: '2026-07-19T08:01:00Z',
    }])
    mockedListMessages.mockResolvedValue([{
      message_id: 'maintenance-message',
      sequence: 1,
      role: 'ASSISTANT',
      content: '设备报修已进入审批。',
      client_message_id: null,
      created_at: '2026-07-19T08:01:00Z',
      agent_result: {
        request_id: 'maintenance-request',
        intent: 'MAINTENANCE_REQUEST',
        reply: '设备报修已进入审批。',
        scenario_key: 'equipment_maintenance',
        knowledge: null,
        scenario_summary: {
          equipment_code: 'EQ-1001',
          equipment_name: '一号生产线泵组',
          equipment_status: 'DEGRADED',
          equipment_version: 7,
          criticality: 'HIGH',
          risk_level: 'LOW',
          approval_route: 'EQUIPMENT_RESPONSIBLE_MANAGER',
          responsible_manager_id: 'MGR-2001',
          recent_maintenance_count: 2,
          internal_prompt: '不应显示的内部字段',
        },
        workflow: {
          scenario_key: 'equipment_maintenance',
          workflow_run_id: 'maintenance-workflow',
          service_request_id: 'maintenance-service',
          ticket_id: 'maintenance-ticket',
          workflow_state: 'WAITING_APPROVAL',
          workflow_version: 3,
          approval_id: 'maintenance-approval',
          missing_fields: [],
        },
        trace: [{
          agent_name: 'maintenance_domain',
          capability: 'read_only_equipment_context',
          status: 'SUCCEEDED',
          summary: 'resolved equipment status, owner and maintenance history',
        }],
      },
    }])

    render(<ChatPage />)

    expect(await screen.findByText('设备报修申请')).toBeInTheDocument()
    expect(screen.getByText('EQ-1001')).toBeInTheDocument()
    expect(screen.getByText('一号生产线泵组')).toBeInTheDocument()
    expect(screen.getByText('降级运行')).toBeInTheDocument()
    expect(screen.getByText('高')).toBeInTheDocument()
    expect(screen.getByText('低')).toBeInTheDocument()
    expect(screen.getByText('设备责任人审批')).toBeInTheDocument()
    expect(screen.queryByText('DEGRADED')).not.toBeInTheDocument()
    expect(screen.queryByText('EQUIPMENT_RESPONSIBLE_MANAGER')).not.toBeInTheDocument()
    expect(screen.getByText('设备维修业务智能体')).toBeInTheDocument()
    expect(screen.queryByText('不应显示的内部字段')).not.toBeInTheDocument()
  })

  it('renames a conversation from its management menu', async () => {
    const original = {
      conversation_id: 'conversation-1',
      title: null,
      scenario_key: 'access_management',
      workflow_run_id: 'workflow-1',
      status: 'OPEN' as const,
      message_count: 2,
      created_at: '2026-07-17T08:00:00Z',
      updated_at: '2026-07-17T08:01:00Z',
    }
    mockedListConversations
      .mockResolvedValueOnce([original])
      .mockResolvedValueOnce([{ ...original, title: '华东客户CRM权限' }])
    mockedListMessages.mockResolvedValue([])
    mockedRename.mockResolvedValue({ ...original, title: '华东客户CRM权限' })

    render(<ChatPage />)
    fireEvent.click(await screen.findByRole('button', { name: '管理会话：权限服务申请' }))
    fireEvent.click(await screen.findByText('重命名'))
    fireEvent.change(screen.getByLabelText('会话名称'), {
      target: { value: '华东客户CRM权限' },
    })
    fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => {
      expect(mockedRename).toHaveBeenCalledWith(
        'conversation-1',
        '华东客户CRM权限',
      )
    })
    expect(await screen.findByText('华东客户CRM权限')).toBeInTheDocument()
  })

  it('deletes a conversation and clears the selected chat', async () => {
    const original = {
      conversation_id: 'conversation-1',
      title: '临时会话',
      scenario_key: null,
      workflow_run_id: null,
      status: 'OPEN' as const,
      message_count: 1,
      created_at: '2026-07-17T08:00:00Z',
      updated_at: '2026-07-17T08:01:00Z',
    }
    mockedListConversations
      .mockResolvedValueOnce([original])
      .mockResolvedValueOnce([])
    mockedListMessages.mockResolvedValue([])
    mockedDelete.mockResolvedValue()

    render(<ChatPage />)
    fireEvent.click(await screen.findByRole('button', { name: '管理会话：临时会话' }))
    fireEvent.click(await screen.findByText('删除'))
    fireEvent.click(await screen.findByRole('button', { name: /删\s*除/ }))

    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith('conversation-1')
    })
    expect(await screen.findByText('今天需要办理什么企业服务？')).toBeInTheDocument()
  })
})
