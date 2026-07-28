import { useEffect, useMemo, useState } from 'react'
import { Button, Dropdown, Empty, Input, Modal, Spin, Tag, message as toast } from 'antd'

import { publicErrorMessage } from '../api/client'
import type {
  ConversationMessage,
  ConversationSummary,
  KnowledgeCitation,
  MultiAgentResult,
} from '../api/contracts'
import {
  deleteConversation,
  listConversations,
  listMessages,
  renameConversation,
  sendAgentMessage,
} from '../api/conversations'
import {
  missingFieldLabel,
  safeDisplayValue,
  safeScenarioSummary,
  scenarioConversationLabel,
} from '../presentation/scenarios'
import { useAuthStore } from '../stores/authStore'

const { TextArea } = Input

const workflowStateLabels: Record<string, string> = {
  CREATED: '已创建',
  RUNNING: '处理中',
  WAITING_USER: '等待补充信息',
  WAITING_APPROVAL: '等待审批',
  WAITING_HUMAN: '等待人工处理',
  EXECUTING: '正在执行',
  COMPLETED: '已完成',
  FAILED: '执行失败',
  CANCELLED: '已取消',
}

const agentLabels: Record<string, string> = {
  supervisor: '任务理解智能体',
  knowledge: '知识检索智能体',
  access_domain: '权限业务智能体',
  maintenance_domain: '设备维修业务智能体',
  employee_lifecycle_domain: '员工生命周期业务智能体',
  procurement_domain: '采购业务智能体',
  policy_engine: '确定性策略引擎',
  orchestrator: '流程编排器',
}

const capabilityLabels: Record<string, string> = {
  intent_and_field_extraction: '意图识别与字段提取',
  read_only_enterprise_context: '查询企业系统上下文',
  authorized_hybrid_retrieval: '企业知识混合检索',
  start_authoritative_workflow: '创建权威业务流程',
  resume_authoritative_workflow: '恢复权威业务流程',
  read_only_equipment_context: '查询设备及维修上下文',
  deterministic_maintenance_policy: '设备维修确定性规则',
  start_maintenance_information_collection: '创建设备报修流程',
  resume_maintenance_information_collection: '恢复设备报修流程',
  bound_workflow_routing: '按已有流程恢复业务场景',
  deterministic_explicit_field_recovery: '读取明确填写的设备编号',
  bounded_supplement_field_filtering: '过滤非待补字段',
  deterministic_supplement_field_recovery: '读取明确的设备补充信息',
  read_only_employee_lifecycle_context: '查询员工生命周期企业事实',
  start_employee_lifecycle_information_collection: '创建员工生命周期流程',
  resume_employee_lifecycle_information_collection: '恢复员工生命周期流程',
  read_only_procurement_context: '查询采购制度与企业上下文',
  start_procurement_information_collection: '创建采购申请流程',
  resume_procurement_information_collection: '恢复采购申请流程',
  user_visible_summary: '生成用户回复',
}

const traceStatusLabels: Record<string, string> = {
  SUCCEEDED: '成功',
  UNAVAILABLE: '暂不可用',
  FALLBACK: '已降级',
  FAILED: '失败',
  CORRECTED: '已校正',
}

const intentLabels: Record<string, string> = {
  ACCESS_REQUEST: '企业系统权限申请',
  MAINTENANCE_REQUEST: '工业设备报修与维护',
  EMPLOYEE_ONBOARDING: '员工入职协同',
  EMPLOYEE_TRANSFER: '员工调岗协同',
  EMPLOYEE_OFFBOARDING: '员工离职协同',
  OFFICE_PROCUREMENT_REQUEST: '采购与办公申请',
  KNOWLEDGE_QUESTION: '企业知识问答',
  UNKNOWN: '暂未识别',
}

function traceSummaryLabel(summary: string): string {
  const routedIntent = summary.match(/^routed intent (.+)$/)
  if (routedIntent) {
    return `已识别为“${intentLabels[routedIntent[1]] || routedIntent[1]}”`
  }

  const citationCount = summary.match(/^returned (\d+) citations$/)
  if (citationCount) return `检索到 ${citationCount[1]} 条参考依据`

  const workflowState = summary.match(/^workflow entered (.+)$/)
  if (workflowState) {
    return `工作流已进入“${workflowStateLabels[workflowState[1]] || workflowState[1]}”`
  }

  const policyOutcome = summary.match(/^routed outcome (.+)$/)
  if (policyOutcome) return `确定性策略结果：${policyOutcome[1]}`

  const knownSummaries: Record<string, string> = {
    'resolved manager and current access facts': '已核对直属领导和当前权限',
    'resolved equipment status, owner and maintenance history': '已核对设备状态、负责人和维修历史',
    'knowledge evidence unavailable; policy remains authoritative': '知识依据暂不可用，确定性策略仍然有效',
    'used deterministic reply after summary failure': '模型总结不可用，已使用确定性回复',
    'kept supplement on the authoritative workflow scenario': '补充信息已继续进入原业务流程',
    'recovered an explicit equipment code from the message': '已读取消息中明确填写的设备编号',
    '已完成采购信息收集与只读事实核对；未生成审批或写操作。': '已核对采购申请所需的企业事实',
    'ignored model fields that were not requested by the workflow': '已忽略模型擅自生成的非待补字段',
  }
  if (summary.startsWith('recovered explicit maintenance supplement fields:')) {
    return '已读取明确填写的发现时间、生产影响或安全观察'
  }
  return knownSummaries[summary] || '已记录安全执行结果'
}

function conversationLabel(conversation: ConversationSummary): string {
  return conversation.title?.trim()
    || scenarioConversationLabel(conversation.scenario_key)
}

function CitationCard({ citation }: { citation: KnowledgeCitation }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <article>
      <div className="citation-heading">
        <strong>{citation.title}</strong>
        <Tag>{citation.version_label}</Tag>
      </div>
      <p className={`citation-excerpt ${expanded ? 'expanded' : 'collapsed'}`}>
        {citation.excerpt}
      </p>
      <div className="citation-meta">
        <small>{citation.source_department}</small>
        <button
          type="button"
          className="citation-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? '收起完整引用' : '展开完整引用'}
        </button>
      </div>
    </article>
  )
}

function AssistantDetails({ result }: { result: MultiAgentResult }) {
  const workflow = result.workflow
  const citations = result.knowledge?.citations || []
  const scenarioFields = safeScenarioSummary(result.scenario_key, result.scenario_summary)
  return (
    <div className="assistant-details">
      {workflow && (
        <div className="workflow-summary">
          <div>
            <span>流程状态</span>
            <strong>{workflowStateLabels[workflow.workflow_state] || workflow.workflow_state}</strong>
          </div>
          {workflow.ticket_id && <Tag color="cyan">工单已创建</Tag>}
          {workflow.missing_fields.length > 0 && (
            <p>
              待补充：{workflow.missing_fields.map(missingFieldLabel).join('、')}
            </p>
          )}
        </div>
      )}
      {scenarioFields.length > 0 && (
        <div className="scenario-summary" aria-label="业务摘要">
          <span className="detail-label">业务摘要</span>
          <dl>
            {scenarioFields.map((field) => (
              <div key={field.key}><dt>{field.label}</dt><dd>{safeDisplayValue(field.value)}</dd></div>
            ))}
          </dl>
        </div>
      )}
      {citations.length > 0 && (
        <div className="citation-list">
          <span className="detail-label">参考依据</span>
          {citations.map((citation) => (
            <CitationCard key={citation.chunk_id} citation={citation} />
          ))}
        </div>
      )}
      <details className="trace-details">
        <summary>查看安全执行轨迹 · {result.trace.length}步</summary>
        {result.trace.map((event, index) => (
          <div key={`${event.agent_name}-${index}`}>
            <span>{agentLabels[event.agent_name] || '业务智能体'}</span>
            <strong>{traceStatusLabels[event.status] || '已记录'}</strong>
            <p>
              {capabilityLabels[event.capability] || '安全能力'}：{traceSummaryLabel(event.summary)}
            </p>
          </div>
        ))}
      </details>
    </div>
  )
}

function MessageBubble({ item }: { item: ConversationMessage }) {
  return (
    <div className={`message-row ${item.role.toLowerCase()}`}>
      <div className="message-bubble">
        <span className="message-role">{item.role === 'USER' ? '你' : 'BizOrch'}</span>
        <p>{item.content}</p>
        {item.agent_result && <AssistantDetails result={item.agent_result} />}
      </div>
    </div>
  )
}

export function ChatPage() {
  const user = useAuthStore((state) => state.user)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [renamingConversation, setRenamingConversation] = useState<ConversationSummary>()
  const [renameDraft, setRenameDraft] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const [deletingConversation, setDeletingConversation] = useState<ConversationSummary>()
  const [deleting, setDeleting] = useState(false)
  const canManageEmployeeLifecycle = Boolean(
    user?.roles.some((role) => role === 'hr' || role === 'admin'),
  )
  const canRequestProcurement = Boolean(user?.roles.includes('employee'))

  const selected = useMemo(
    () => conversations.find((item) => item.conversation_id === selectedId),
    [conversations, selectedId],
  )

  const refreshConversations = async (preferredId?: string) => {
    const items = await listConversations()
    setConversations(items)
    const next = preferredId || selectedId || items[0]?.conversation_id
    setSelectedId(next)
    return next
  }

  useEffect(() => {
    const load = async () => {
      try {
        const id = await refreshConversations()
        if (id) setMessages(await listMessages(id))
      } catch (error) {
        toast.error(publicErrorMessage(error))
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const selectConversation = async (id: string) => {
    setSelectedId(id)
    setLoading(true)
    try {
      setMessages(await listMessages(id))
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  const newConversation = () => {
    setSelectedId(undefined)
    setMessages([])
    setDraft('')
  }

  const openRename = (conversation: ConversationSummary) => {
    setRenamingConversation(conversation)
    setRenameDraft(conversationLabel(conversation))
  }

  const saveRename = async () => {
    const title = renameDraft.trim()
    if (!renamingConversation || !title || savingTitle) return
    setSavingTitle(true)
    try {
      await renameConversation(renamingConversation.conversation_id, title)
      setConversations(await listConversations())
      setRenamingConversation(undefined)
      toast.success('会话名称已更新')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSavingTitle(false)
    }
  }

  const confirmDelete = async () => {
    if (!deletingConversation || deleting) return
    setDeleting(true)
    try {
      const deletedId = deletingConversation.conversation_id
      await deleteConversation(deletedId)
      const items = await listConversations()
      setConversations(items)
      if (selectedId === deletedId) {
        const nextId = items[0]?.conversation_id
        setSelectedId(nextId)
        setMessages(nextId ? await listMessages(nextId) : [])
      }
      setDeletingConversation(undefined)
      toast.success('会话已删除')
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setDeleting(false)
    }
  }

  const send = async () => {
    const content = draft.trim()
    if (!content || sending) return
    setSending(true)
    setDraft('')
    const clientMessageId = crypto.randomUUID()
    const optimistic: ConversationMessage = {
      message_id: clientMessageId,
      sequence: messages.length + 1,
      role: 'USER',
      content,
      client_message_id: clientMessageId,
      agent_result: null,
      created_at: new Date().toISOString(),
    }
    setMessages((current) => [...current, optimistic])
    try {
      const result = await sendAgentMessage({
        message: content,
        clientMessageId,
        conversationId: selectedId,
      })
      setSelectedId(result.conversation_id)
      setMessages(await listMessages(result.conversation_id))
      await refreshConversations(result.conversation_id)
    } catch (error) {
      setDraft(content)
      setMessages((current) => current.filter((item) => item.message_id !== clientMessageId))
      toast.error(publicErrorMessage(error))
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <div className="service-page">
      <aside className="conversation-panel">
        <div className="panel-heading">
          <span className="eyebrow">CONVERSATIONS</span>
          <h2>服务会话</h2>
        </div>
        <Button type="primary" block onClick={newConversation}>新建会话</Button>
        <div className="conversation-list">
          {conversations.map((conversation) => {
            const label = conversationLabel(conversation)
            return (
              <div
                className={`conversation-item ${conversation.conversation_id === selectedId ? 'active' : ''}`}
                key={conversation.conversation_id}
              >
                <button
                  type="button"
                  className="conversation-select"
                  onClick={() => void selectConversation(conversation.conversation_id)}
                >
                  <strong>{label}</strong>
                  <span>{conversation.message_count} 条消息</span>
                  <small>{new Date(conversation.updated_at).toLocaleString('zh-CN')}</small>
                </button>
                <Dropdown
                  trigger={['click']}
                  menu={{
                    items: [
                      { key: 'rename', label: '重命名' },
                      { key: 'delete', label: '删除', danger: true },
                    ],
                    onClick: ({ key, domEvent }) => {
                      domEvent.stopPropagation()
                      if (key === 'rename') openRename(conversation)
                      if (key === 'delete') setDeletingConversation(conversation)
                    },
                  }}
                >
                  <Button
                    type="text"
                    className="conversation-menu"
                    aria-label={`管理会话：${label}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    ···
                  </Button>
                </Dropdown>
              </div>
            )
          })}
        </div>
      </aside>

      <section className="chat-workspace">
        <header className="chat-header">
          <div>
            <span className="eyebrow">SMART SERVICE DESK</span>
            <h1>智能服务台</h1>
            <p>描述系统权限、设备报修、采购办公或员工生命周期需求，也可询问企业制度。</p>
          </div>
          <Tag color={selected?.workflow_run_id ? 'cyan' : 'default'}>
            {selected?.workflow_run_id ? '已关联业务流程' : '安全对话'}
          </Tag>
        </header>

        <div className="message-stream" aria-live="polite">
          {loading ? (
            <div className="stream-state"><Spin /></div>
          ) : messages.length === 0 ? (
            <div className="welcome-state">
              <span className="welcome-mark">BO</span>
              <h2>今天需要办理什么企业服务？</h2>
              <p>例如：申请 CRM 只读权限，或报修产线设备异响。</p>
              <div className="quick-prompts">
                <button type="button" onClick={() => setDraft('申请CRM只读权限30天，用于客户项目支持。')}>申请系统权限</button>
                <button type="button" onClick={() => setDraft('设备 PRESS-001 出现异常振动，今天 10:30 发现，生产速度下降，暂未发现明显安全风险，请安排维修。')}>发起设备报修</button>
                <button type="button" onClick={() => setDraft('VPN权限需要经过谁审批？')}>查询审批制度</button>
                {canRequestProcurement && (
                  <button type="button" onClick={() => setDraft('申请采购办公用品：A4打印纸2箱，类别为办公耗材，预计金额260元，使用成本中心 CC-SALES-EAST-001，期望到货日期为2026-08-05，送到上海总部行政前台，用于新员工入职办公区补充。')}>申请办公采购</button>
                )}
                {canManageEmployeeLifecycle && (
                  <>
                    <button type="button" onClick={() => setDraft('请为新员工 EMP-3001 王晨办理今天入职，部门是生产管理部，岗位是生产计划专员，直属负责人是 EMP-MANAGER，办公地点为上海总部，按该岗位的标准权限和设备配置办理。')}>办理员工入职</button>
                    <button type="button" onClick={() => setDraft('将员工 EMP-2001 今天调到设备工程部，岗位调整为设备维护工程师，直属负责人改为 EMP-MAINT-MANAGER，办公地点为华东生产基地，请同步处理岗位基线权限和设备调整。')}>办理员工调岗</button>
                    <button type="button" onClick={() => setDraft('请为员工 EMP-2001 办理今天离职，原因是劳动合同到期，请停用企业账号、撤销有效权限并创建资产归还任务。')}>办理员工离职</button>
                  </>
                )}
              </div>
            </div>
          ) : (
            messages.map((item) => <MessageBubble key={item.message_id} item={item} />)
          )}
          {sending && <div className="assistant-thinking"><Spin size="small" /> 正在理解并核对企业信息…</div>}
        </div>

        <footer className="composer">
          <TextArea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            autoSize={{ minRows: 2, maxRows: 5 }}
            placeholder="描述你的需求，Enter发送，Shift + Enter换行"
            disabled={sending}
          />
          <div className="composer-actions">
            <span>AI只提出方案，审批和执行由确定性规则控制</span>
            <Button type="primary" onClick={() => void send()} loading={sending} disabled={!draft.trim()}>
              发送
            </Button>
          </div>
        </footer>
        </section>
      </div>

      <Modal
        title="重命名会话"
        open={Boolean(renamingConversation)}
        okText="保存"
        cancelText="取消"
        confirmLoading={savingTitle}
        okButtonProps={{ disabled: !renameDraft.trim() }}
        onOk={() => void saveRename()}
        onCancel={() => setRenamingConversation(undefined)}
      >
        <Input
          aria-label="会话名称"
          value={renameDraft}
          maxLength={80}
          showCount
          autoFocus
          onChange={(event) => setRenameDraft(event.target.value)}
          onPressEnter={() => void saveRename()}
        />
      </Modal>

      <Modal
        title="删除这个会话？"
        open={Boolean(deletingConversation)}
        okText="删除"
        cancelText="取消"
        confirmLoading={deleting}
        okButtonProps={{ danger: true }}
        onOk={() => void confirmDelete()}
        onCancel={() => setDeletingConversation(undefined)}
      >
        <p>会话将从列表中移除；已经创建的工单、审批、工作流和审计记录会继续保留。</p>
      </Modal>
    </>
  )
}
