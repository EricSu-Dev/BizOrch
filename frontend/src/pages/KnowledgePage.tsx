import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Empty,
  Input,
  Modal,
  Pagination,
  Select,
  Skeleton,
  Tabs,
  Tag,
  message as toast,
} from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { isRequestTimeout, publicErrorMessage } from '../api/client'
import type {
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  KnowledgeDocumentEvent,
  KnowledgeIndexJob,
  KnowledgeIndexStatus,
  KnowledgeLifecycleResult,
  KnowledgePublicationStatus,
  KnowledgeSearchResult,
  KnowledgeTrustLevel,
  RoleName,
} from '../api/contracts'
import {
  getKnowledgeDocument,
  listKnowledgeDocumentEvents,
  listKnowledgeDocuments,
  listKnowledgeIndexJobs,
  publishKnowledgeDocument,
  retireKnowledgeDocument,
  retryKnowledgeIndex,
  retryKnowledgeVectorCleanup,
  searchKnowledge,
  uploadKnowledgeDocument,
} from '../api/knowledge'
import {
  defaultKnowledgeSearchQuestion,
  formatKnowledgeDate,
  indexJobStatusView,
  indexStatusView,
  knowledgeEventLabel,
  knowledgeSpaceLabel,
  knowledgeSpaceOptions,
  publicationStatusView,
  trustLevelLabel,
} from '../presentation/knowledge'
import {
  roleLabel,
} from '../presentation/identity'
import { useAuthStore } from '../stores/authStore'

type KnowledgeAction = 'publish' | 'retire' | 'retry-index' | 'cleanup'

interface UploadDraft {
  file?: File
  knowledgeSpace: string
  title: string
  sourceUri: string
  versionLabel: string
  sourceDepartment: string
  trustLevel: KnowledgeTrustLevel
  effectiveFrom: string
  effectiveUntil: string
  allowedRoles: RoleName[]
  allowedUserIds: string
}

const emptyUploadDraft: UploadDraft = {
  knowledgeSpace: 'access_and_security',
  title: '',
  sourceUri: '',
  versionLabel: '2026.1',
  sourceDepartment: '',
  trustLevel: 'HIGH',
  effectiveFrom: '',
  effectiveUntil: '',
  allowedRoles: ['employee', 'approver'],
  allowedUserIds: '',
}

const knowledgeRoleOptions = (Object.keys(roleLabel) as RoleName[]).map(
  (value) => ({
    value,
    label: `${roleLabel[value]}（${value}）`,
  }),
)

function formatAllowedRoles(roles: readonly string[]): string {
  if (roles.length === 0) return '未限定角色'
  return roles
    .map((role) => {
      const knownRole = role as RoleName
      return roleLabel[knownRole] ? `${roleLabel[knownRole]}（${role}）` : role
    })
    .join('、')
}

const actionCopy: Record<
  KnowledgeAction,
  { title: string; content: string; success: string; danger?: boolean }
> = {
  publish: {
    title: '确认发布知识文档',
    content: '发布后，满足访问范围的员工可以在智能服务台检索到该版本。',
    success: '知识文档已发布',
  },
  retire: {
    title: '确认下线知识文档',
    content: '下线是不可恢复操作，文档会立即退出员工检索，但审计历史仍会保留。',
    success: '知识文档已下线',
    danger: true,
  },
  'retry-index': {
    title: '确认重新建立索引',
    content: '系统会创建一条持久化索引任务，由独立 Worker 异步处理。',
    success: '重新索引任务已创建',
  },
  cleanup: {
    title: '重新清理派生向量',
    content: '该操作只清理已下线文档的派生向量，不会改变权威生命周期状态。',
    success: '向量清理操作已完成',
  },
}

function localDateTimeToIso(value: string): string | undefined {
  return value ? new Date(value).toISOString() : undefined
}

function KnowledgeSearchResults({ result }: { result: KnowledgeSearchResult }) {
  if (result.citations.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="当前身份没有检索到可引用的已发布知识。被权限、版本、生效期或下线状态过滤的文档不会泄露任何信息。"
      />
    )
  }
  return (
    <div className="knowledge-search-results">
      <div className="knowledge-search-result-summary">
        <strong>找到 {result.citations.length} 个可引用片段</strong>
        <span className="full-identifier">检索编号 {result.retrieval_id}</span>
      </div>
      {result.citations.map((citation) => (
        <details key={citation.chunk_id}>
          <summary>
            <span>{citation.title} · 版本 {citation.version_label}</span>
            <Tag>{Math.round(citation.combined_score * 100)}% 相关</Tag>
          </summary>
          <p>{citation.excerpt}</p>
          <small>
            {citation.source_department} · {trustLevelLabel[citation.trust_level]}
            {' · '}来源 {citation.source_uri}
          </small>
        </details>
      ))}
    </div>
  )
}

function KnowledgeListItem({
  document,
  onView,
}: {
  document: KnowledgeDocument
  onView: () => void
}) {
  const publication = publicationStatusView[document.publication_status]
  const index = indexStatusView[document.index_status]
  return (
    <article className="knowledge-list-item">
      <div className="knowledge-list-item-top">
        <span className="knowledge-space-label">
          {knowledgeSpaceLabel(document.knowledge_space)}
        </span>
        <div className="knowledge-state-tags">
          <Tag color={publication.color}>{publication.label}</Tag>
          <Tag color={index.color}>{index.label}</Tag>
        </div>
      </div>
      <div className="knowledge-list-item-body">
        <h3>{document.title}</h3>
        <p className="full-identifier">{document.source_uri}</p>
        <dl>
          <div>
            <dt>来源部门</dt>
            <dd>{document.source_department}</dd>
          </div>
          <div>
            <dt>当前版本</dt>
            <dd>{document.version_label}</dd>
          </div>
        </dl>
      </div>
      <footer className="knowledge-list-item-foot">
        <small>更新于 {formatKnowledgeDate(document.updated_at)}</small>
        <Button type="link" onClick={onView}>查看详情</Button>
      </footer>
    </article>
  )
}

function JobCard({ job, latest }: { job: KnowledgeIndexJob; latest: boolean }) {
  const status = indexJobStatusView[job.status]
  return (
    <article className="knowledge-job-card">
      <div>
        <strong>{latest ? '最近索引任务' : '历史索引任务'}</strong>
        <Tag color={status.color}>{status.label}</Tag>
      </div>
      <p>已尝试 {job.attempt_count} / {job.max_attempts} 次</p>
      {job.last_error_message && (
        <p className="knowledge-job-error">{job.last_error_message}</p>
      )}
      <small>{formatKnowledgeDate(job.updated_at)} · 任务编号 {job.job_id}</small>
    </article>
  )
}

function EventCard({ event }: { event: KnowledgeDocumentEvent }) {
  return (
    <li>
      <span className="knowledge-event-dot" />
      <article>
        <div>
          <strong>{knowledgeEventLabel(event.event_type)}</strong>
          <span>{formatKnowledgeDate(event.created_at)}</span>
        </div>
        <p>操作人 {event.actor_id} · {event.actor_roles.join('、') || '系统'}</p>
      </article>
    </li>
  )
}

function KnowledgeDetailView({
  detail,
  jobs,
  events,
  polling,
  onAction,
}: {
  detail: KnowledgeDocumentDetail
  jobs: KnowledgeIndexJob[]
  events: KnowledgeDocumentEvent[]
  polling: boolean
  onAction: (action: KnowledgeAction) => void
}) {
  const document = detail.document
  const publication = publicationStatusView[document.publication_status]
  const index = indexStatusView[document.index_status]
  const canPublish =
    document.publication_status === 'DRAFT' && document.index_status === 'INDEXED'
  const canRetire = document.publication_status !== 'RETIRED'
  const canRetryIndex =
    document.publication_status !== 'RETIRED' && document.index_status === 'FAILED'

  return (
    <div className="knowledge-detail">
      <header className="knowledge-detail-header">
        <div>
          <span className="eyebrow">KNOWLEDGE DOCUMENT</span>
          <h1>{document.title}</h1>
          <p className="full-identifier">来源 {document.source_uri}</p>
        </div>
        <div className="knowledge-status-box">
          <div>
            <Tag color={publication.color}>{publication.label}</Tag>
            <Tag color={index.color}>{index.label}</Tag>
          </div>
          <strong>版本 {document.version_label}</strong>
          <small>{polling ? '正在自动刷新索引状态' : `更新于 ${formatKnowledgeDate(document.updated_at)}`}</small>
        </div>
      </header>

      <section className="knowledge-actions" aria-label="知识文档操作">
        <div>
          <strong>生命周期操作</strong>
          <p>索引完成不等于发布，所有状态变化都会留下审计事件。</p>
        </div>
        <div>
          {canRetryIndex && <Button onClick={() => onAction('retry-index')}>重新索引</Button>}
          {document.publication_status === 'RETIRED' && (
            <Button onClick={() => onAction('cleanup')}>重试向量清理</Button>
          )}
          {canRetire && <Button danger onClick={() => onAction('retire')}>下线</Button>}
          <Button type="primary" disabled={!canPublish} onClick={() => onAction('publish')}>
            发布
          </Button>
        </div>
      </section>

      <Tabs
        className="knowledge-detail-tabs"
        defaultActiveKey="overview"
        items={[
          {
            key: 'overview',
            label: '基本信息',
            children: (
              <section className="knowledge-overview">
                <div><span>文档类别</span><strong>{knowledgeSpaceLabel(document.knowledge_space)}</strong></div>
                <div><span>来源部门</span><strong>{document.source_department}</strong></div>
                <div><span>可信等级</span><strong>{trustLevelLabel[document.trust_level]}</strong></div>
                <div><span>生效时间</span><strong>{formatKnowledgeDate(document.effective_from)}</strong></div>
                <div><span>失效时间</span><strong>{formatKnowledgeDate(document.effective_until)}</strong></div>
                <div><span>分块数量</span><strong>{document.chunk_count}</strong></div>
                <div><span>允许检索角色</span><strong>{formatAllowedRoles(document.allowed_roles)}</strong></div>
                <div><span>允许用户</span><strong>{document.allowed_user_ids.join('、') || '未限定用户'}</strong></div>
                <div><span>原文件</span><strong>{document.original_filename || '兼容入库文本'}</strong></div>
              </section>
            ),
          },
          {
            key: 'index',
            label: `索引与片段 (${document.chunk_count})`,
            children: (
              <div className="knowledge-tab-sections">
                <section className="knowledge-section">
                  <div className="section-title">
                    <div><span className="eyebrow">INDEX JOBS</span><h2>索引任务</h2></div>
                    <span>{jobs.length} 条任务记录</span>
                  </div>
                  <div className="knowledge-job-list">
                    {jobs.length > 0
                      ? jobs.map((job, indexValue) => (
                          <JobCard key={job.job_id} job={job} latest={indexValue === 0} />
                        ))
                      : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无索引任务" />}
                  </div>
                </section>

                <section className="knowledge-section">
                  <div className="section-title">
                    <div><span className="eyebrow">SAFE EXCERPTS</span><h2>检索分块摘要</h2></div>
                    <span>默认折叠，不展示完整原文</span>
                  </div>
                  <div className="knowledge-chunk-list">
                    {detail.chunks.map((chunk) => (
                      <details key={chunk.chunk_id}>
                        <summary>片段 {chunk.chunk_index + 1} · {chunk.char_count} 字符</summary>
                        <p>{chunk.excerpt}</p>
                        <small className="full-identifier">片段编号 {chunk.chunk_id}</small>
                      </details>
                    ))}
                  </div>
                </section>
              </div>
            ),
          },
          {
            key: 'audit',
            label: `审计轨迹 (${events.length})`,
            children: (
              <section className="knowledge-section">
                <div className="section-title">
                  <div><span className="eyebrow">AUDIT TIMELINE</span><h2>知识审计轨迹</h2></div>
                  <span>{events.length} 个生命周期事件</span>
                </div>
                <ol className="knowledge-event-list">
                  {events.map((event) => <EventCard key={event.event_id} event={event} />)}
                </ol>
              </section>
            ),
          },
        ]}
      />

      <footer className="knowledge-detail-foot full-identifier">
        文档编号 {document.document_id} · 索引版本 {document.index_version}
      </footer>
    </div>
  )
}

export function KnowledgePage() {
  const { documentId } = useParams<{ documentId: string }>()
  const navigate = useNavigate()
  const currentUser = useAuthStore((state) => state.user)
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keywordDraft, setKeywordDraft] = useState('')
  const [keyword, setKeyword] = useState('')
  const [knowledgeSpace, setKnowledgeSpace] = useState('')
  const [sourceDepartment, setSourceDepartment] = useState('')
  const [publicationStatus, setPublicationStatus] =
    useState<KnowledgePublicationStatus>()
  const [indexStatus, setIndexStatus] = useState<KnowledgeIndexStatus>()
  const [loadingList, setLoadingList] = useState(true)
  const [detail, setDetail] = useState<KnowledgeDocumentDetail>()
  const [jobs, setJobs] = useState<KnowledgeIndexJob[]>([])
  const [events, setEvents] = useState<KnowledgeDocumentEvent[]>([])
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [pendingAction, setPendingAction] = useState<KnowledgeAction>()
  const [submittingAction, setSubmittingAction] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadDraft, setUploadDraft] = useState<UploadDraft>(emptyUploadDraft)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState(
    defaultKnowledgeSearchQuestion('access_and_security'),
  )
  const [searchSpace, setSearchSpace] = useState('access_and_security')
  const [searchTopK, setSearchTopK] = useState(5)
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResult>()
  const pollGeneration = useRef(0)

  const loadDocuments = useCallback(async () => {
    setLoadingList(true)
    try {
      const result = await listKnowledgeDocuments({
        page,
        page_size: 10,
        keyword: keyword || undefined,
        knowledge_space: knowledgeSpace.trim() || undefined,
        source_department: sourceDepartment.trim() || undefined,
        publication_status: publicationStatus,
        index_status: indexStatus,
      })
      setDocuments(result.items)
      setTotal(result.total)
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setLoadingList(false)
    }
  }, [
    indexStatus,
    keyword,
    knowledgeSpace,
    page,
    publicationStatus,
    sourceDepartment,
  ])

  const loadDetail = useCallback(async (selectedId: string, showLoading = true) => {
    if (showLoading) setLoadingDetail(true)
    try {
      const [nextDetail, nextJobs, nextEvents] = await Promise.all([
        getKnowledgeDocument(selectedId),
        listKnowledgeIndexJobs(selectedId),
        listKnowledgeDocumentEvents(selectedId),
      ])
      setDetail(nextDetail)
      setJobs(nextJobs.items)
      setEvents(nextEvents.items)
      return nextDetail
    } catch (error) {
      toast.error(publicErrorMessage(error))
      return undefined
    } finally {
      if (showLoading) setLoadingDetail(false)
    }
  }, [])

  useEffect(() => {
    void loadDocuments()
  }, [loadDocuments])

  useEffect(() => {
    pollGeneration.current += 1
    if (!documentId) {
      setDetail(undefined)
      setJobs([])
      setEvents([])
      return
    }
    setDetail(undefined)
    setJobs([])
    setEvents([])
    void loadDetail(documentId)
  }, [documentId, loadDetail])

  const activeJobStatus = detail?.latest_index_job?.status
  useEffect(() => {
    if (
      !documentId ||
      (activeJobStatus !== 'PENDING' && activeJobStatus !== 'RUNNING')
    ) {
      return
    }
    const generation = pollGeneration.current
    let cancelled = false
    let attempts = 0
    let timer: ReturnType<typeof setTimeout>

    const poll = () => {
      timer = setTimeout(async () => {
        if (cancelled || generation !== pollGeneration.current) return
        attempts += 1
        const refreshed = await loadDetail(documentId, false)
        const status = refreshed?.latest_index_job?.status
        if (
          !cancelled &&
          attempts < 40 &&
          (status === 'PENDING' || status === 'RUNNING')
        ) {
          poll()
        } else {
          void loadDocuments()
        }
      }, 3000)
    }
    poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeJobStatus, documentId, loadDetail, loadDocuments])

  const executeAction = async () => {
    if (!documentId || !pendingAction) return
    setSubmittingAction(true)
    try {
      let result: KnowledgeLifecycleResult | KnowledgeIndexJob
      if (pendingAction === 'publish') {
        result = await publishKnowledgeDocument(documentId)
      } else if (pendingAction === 'retire') {
        result = await retireKnowledgeDocument(documentId)
      } else if (pendingAction === 'retry-index') {
        result = await retryKnowledgeIndex(documentId)
      } else {
        result = await retryKnowledgeVectorCleanup(documentId)
      }
      void result
      toast.success(actionCopy[pendingAction].success)
      setPendingAction(undefined)
      await Promise.all([loadDetail(documentId, false), loadDocuments()])
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSubmittingAction(false)
    }
  }

  const openSearchWorkbench = () => {
    const nextSpace =
      detail?.document.knowledge_space ||
      knowledgeSpace.trim() ||
      'access_and_security'
    setSearchSpace(nextSpace)
    setSearchQuery(defaultKnowledgeSearchQuestion(nextSpace))
    setSearchResult(undefined)
    setSearchOpen(true)
  }

  const submitSearch = async () => {
    if (!searchQuery.trim() || !searchSpace.trim() || searching) return
    setSearching(true)
    try {
      setSearchResult(await searchKnowledge({
        query: searchQuery,
        knowledge_space: searchSpace,
        top_k: searchTopK,
      }))
    } catch (error) {
      toast.error(publicErrorMessage(error))
    } finally {
      setSearching(false)
    }
  }

  const submitUpload = async () => {
    const draft = uploadDraft
    if (
      !draft.file ||
      !draft.title.trim() ||
      !draft.knowledgeSpace.trim() ||
      !draft.sourceUri.trim() ||
      !draft.versionLabel.trim() ||
      !draft.sourceDepartment.trim()
    ) {
      toast.warning('请填写全部必填信息并选择知识文件。')
      return
    }
    setUploading(true)
    try {
      const result = await uploadKnowledgeDocument({
        file: draft.file,
        knowledge_space: draft.knowledgeSpace,
        title: draft.title,
        source_uri: draft.sourceUri,
        version_label: draft.versionLabel,
        source_department: draft.sourceDepartment,
        trust_level: draft.trustLevel,
        effective_from: localDateTimeToIso(draft.effectiveFrom),
        effective_until: localDateTimeToIso(draft.effectiveUntil),
        allowed_roles: draft.allowedRoles,
        allowed_user_ids: draft.allowedUserIds
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      })
      setUploadOpen(false)
      setUploadDraft(emptyUploadDraft)
      setPage(1)
      toast.success('知识文档已登记，索引任务正在排队。')
      navigate(`/knowledge/${result.document.document_id}`)
      await loadDocuments()
    } catch (error) {
      toast.error(
        isRequestTimeout(error)
          ? '上传等待超时，服务端可能已经完成登记。请先刷新文档列表确认结果，避免重复上传。'
          : publicErrorMessage(error),
      )
    } finally {
      setUploading(false)
    }
  }

  const action = pendingAction ? actionCopy[pendingAction] : undefined

  return (
    <div className="knowledge-page">
      <main className="knowledge-catalog">
        <header className="knowledge-catalog-header">
          <div>
            <span className="eyebrow">KNOWLEDGE OPERATIONS</span>
            <h1>企业知识库</h1>
            <p>统一管理知识版本、索引状态、发布范围与生命周期审计。</p>
          </div>
          <div className="knowledge-heading-actions">
            <Button onClick={openSearchWorkbench}>测试检索</Button>
            <Button type="primary" onClick={() => setUploadOpen(true)}>上传文档</Button>
          </div>
        </header>

        <div className="knowledge-filters">
          <Input.Search
            aria-label="搜索知识文档"
            placeholder="搜索标题、来源或版本"
            value={keywordDraft}
            allowClear
            onChange={(event) => setKeywordDraft(event.target.value)}
            onSearch={(value) => {
              setKeyword(value.trim())
              setPage(1)
            }}
          />
          <Select
            aria-label="文档类别筛选"
            placeholder="文档类别"
            allowClear
            showSearch
            optionFilterProp="label"
            value={knowledgeSpace || undefined}
            onChange={(value) => {
              setKnowledgeSpace(value || '')
              setPage(1)
            }}
            options={knowledgeSpaceOptions}
          />
          <Input
            aria-label="来源部门筛选"
            placeholder="来源部门"
            allowClear
            value={sourceDepartment}
            onChange={(event) => {
              setSourceDepartment(event.target.value)
              setPage(1)
            }}
          />
          <Select
            aria-label="发布状态筛选"
            placeholder="发布状态"
            allowClear
            value={publicationStatus}
            onChange={(value) => {
              setPublicationStatus(value)
              setPage(1)
            }}
            options={Object.entries(publicationStatusView).map(([value, view]) => ({
              value,
              label: view.label,
            }))}
          />
          <Select
            aria-label="索引状态筛选"
            placeholder="索引状态"
            allowClear
            value={indexStatus}
            onChange={(value) => {
              setIndexStatus(value)
              setPage(1)
            }}
            options={Object.entries(indexStatusView).map(([value, view]) => ({
              value,
              label: view.label,
            }))}
          />
        </div>

        <div className="knowledge-list-summary">
          <div>
            <strong>知识文档</strong>
            <span>共 {total} 份</span>
          </div>
          <span>点击“查看详情”管理索引、发布和审计信息</span>
        </div>

        <div className="knowledge-list">
          {loadingList ? (
            <div className="knowledge-list-loading">
              <Skeleton active paragraph={{ rows: 8 }} />
            </div>
          ) : documents.length === 0 ? (
            <div className="knowledge-list-empty">
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的知识文档" />
            </div>
          ) : (
            documents.map((document) => (
              <KnowledgeListItem
                key={document.document_id}
                document={document}
                onView={() => navigate(`/knowledge/${document.document_id}`)}
              />
            ))
          )}
        </div>
        {total > 10 && (
          <Pagination
            simple
            current={page}
            pageSize={10}
            total={total}
            onChange={setPage}
          />
        )}
      </main>

      <Modal
        className="knowledge-detail-modal"
        open={Boolean(documentId)}
        width={1180}
        footer={null}
        destroyOnHidden
        mask={{ closable: !submittingAction }}
        keyboard={!submittingAction}
        onCancel={() => {
          if (!submittingAction) navigate('/knowledge')
        }}
      >
        {loadingDetail ? (
          <div className="knowledge-loading"><Skeleton active paragraph={{ rows: 14 }} /></div>
        ) : detail ? (
          <KnowledgeDetailView
            detail={detail}
            jobs={jobs}
            events={events}
            polling={activeJobStatus === 'PENDING' || activeJobStatus === 'RUNNING'}
            onAction={setPendingAction}
          />
        ) : (
          <div className="knowledge-empty">
            <Empty description="无法加载该知识文档，请关闭后重试" />
          </div>
        )}
      </Modal>

      <Modal
        title="按当前身份测试检索"
        open={searchOpen}
        width={720}
        okText="开始检索"
        cancelText="关闭"
        confirmLoading={searching}
        okButtonProps={{ disabled: !searchQuery.trim() || !searchSpace.trim() }}
        onOk={() => void submitSearch()}
        onCancel={() => {
          if (!searching) setSearchOpen(false)
        }}
      >
        <div className="knowledge-search-form">
          <div className="knowledge-identity-notice">
            <strong>当前检索账号：{currentUser?.username || '当前登录用户'}</strong>
            <span>
              {currentUser?.roles.map((role) => roleLabel[role]).join('、') || '已认证用户'}
            </span>
            <p>测试请求不接受员工编号或角色参数，后端只使用当前登录身份执行权限过滤。</p>
          </div>
          <label>
            <span>文档类别</span>
            <Select
              aria-label="测试文档类别"
              value={searchSpace}
              showSearch
              optionFilterProp="label"
              onChange={(value) => {
                setSearchSpace(value)
                setSearchQuery(defaultKnowledgeSearchQuestion(value))
                setSearchResult(undefined)
              }}
              options={knowledgeSpaceOptions}
            />
          </label>
          <label>
            <span>返回片段数</span>
            <Select
              aria-label="返回片段数"
              value={searchTopK}
              onChange={setSearchTopK}
              options={[3, 5, 10].map((value) => ({ value, label: `${value} 个` }))}
            />
          </label>
          <label className="span-two">
            <span>测试问题</span>
            <Input.TextArea
              aria-label="测试问题"
              value={searchQuery}
              maxLength={2000}
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder="例如：VPN 远程访问需要经过谁审批？"
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </label>
          <div className="span-two" aria-live="polite">
            {searchResult && <KnowledgeSearchResults result={searchResult} />}
          </div>
        </div>
      </Modal>

      <Modal
        title="上传企业知识文档"
        open={uploadOpen}
        width={680}
        okText="登记并创建索引任务"
        cancelText="取消"
        confirmLoading={uploading}
        onOk={() => void submitUpload()}
        onCancel={() => {
          if (!uploading) setUploadOpen(false)
        }}
      >
        <div className="knowledge-upload-form">
          {uploading && (
            <div className="knowledge-upload-progress span-two" role="status">
              正在安全保存文件并登记索引任务，请保持页面打开，不要重复提交。
            </div>
          )}
          <label className="span-two">
            <span>知识文件 *</span>
            <input
              aria-label="知识文件"
              type="file"
              accept=".md,.txt,.pdf"
              onChange={(event) =>
                setUploadDraft((current) => ({
                  ...current,
                  file: event.target.files?.[0],
                }))
              }
            />
            <small>支持 Markdown、UTF-8 TXT 和文本型 PDF，最大 10MB。</small>
          </label>
          <label>
            <span>文档标题 *</span>
            <Input value={uploadDraft.title} onChange={(event) => setUploadDraft((current) => ({ ...current, title: event.target.value }))} />
          </label>
          <label>
            <span>版本号 *</span>
            <Input value={uploadDraft.versionLabel} onChange={(event) => setUploadDraft((current) => ({ ...current, versionLabel: event.target.value }))} />
            <small>已下线版本不可恢复；重新上传同一来源时，请填写新的版本号，例如 2026.2。</small>
          </label>
          <label>
            <span>文档类别 *</span>
            <Select
              value={uploadDraft.knowledgeSpace}
              showSearch
              optionFilterProp="label"
              onChange={(value) => setUploadDraft((current) => ({ ...current, knowledgeSpace: value }))}
              options={knowledgeSpaceOptions}
            />
          </label>
          <label>
            <span>来源部门 *</span>
            <Input value={uploadDraft.sourceDepartment} onChange={(event) => setUploadDraft((current) => ({ ...current, sourceDepartment: event.target.value }))} />
          </label>
          <label className="span-two">
            <span>来源标识 *</span>
            <Input placeholder="例如 policy://remote-access" value={uploadDraft.sourceUri} onChange={(event) => setUploadDraft((current) => ({ ...current, sourceUri: event.target.value }))} />
          </label>
          <label>
            <span>可信等级</span>
            <Select
              value={uploadDraft.trustLevel}
              onChange={(value) => setUploadDraft((current) => ({ ...current, trustLevel: value }))}
              options={Object.entries(trustLevelLabel).map(([value, label]) => ({ value, label }))}
            />
          </label>
          <label>
            <span>允许检索角色</span>
            <Select
              mode="multiple"
              value={uploadDraft.allowedRoles}
              onChange={(value) => setUploadDraft((current) => ({ ...current, allowedRoles: value }))}
              options={knowledgeRoleOptions}
            />
            <small>控制哪些登录身份可以通过智能服务台或检索接口引用正文，不控制知识库管理页面的访问权限。</small>
          </label>
          <label>
            <span>生效时间</span>
            <input type="datetime-local" value={uploadDraft.effectiveFrom} onChange={(event) => setUploadDraft((current) => ({ ...current, effectiveFrom: event.target.value }))} />
          </label>
          <label>
            <span>失效时间</span>
            <input type="datetime-local" value={uploadDraft.effectiveUntil} onChange={(event) => setUploadDraft((current) => ({ ...current, effectiveUntil: event.target.value }))} />
          </label>
          <label className="span-two">
            <span>指定允许用户</span>
            <Input placeholder="多个员工编号使用英文逗号分隔" value={uploadDraft.allowedUserIds} onChange={(event) => setUploadDraft((current) => ({ ...current, allowedUserIds: event.target.value }))} />
          </label>
        </div>
      </Modal>

      <Modal
        rootClassName="knowledge-action-confirm-root"
        title={action?.title}
        open={Boolean(action)}
        zIndex={1300}
        okText="确认执行"
        cancelText="取消"
        confirmLoading={submittingAction}
        okButtonProps={{ danger: action?.danger }}
        onOk={() => void executeAction()}
        onCancel={() => {
          if (!submittingAction) setPendingAction(undefined)
        }}
      >
        <p>{action?.content}</p>
      </Modal>
    </div>
  )
}
