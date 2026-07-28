import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  KnowledgeDocumentEvent,
  KnowledgeIndexJob,
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
import { useAuthStore } from '../stores/authStore'
import { KnowledgePage } from './KnowledgePage'

vi.mock('../api/knowledge', () => ({
  getKnowledgeDocument: vi.fn(),
  listKnowledgeDocumentEvents: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  listKnowledgeIndexJobs: vi.fn(),
  publishKnowledgeDocument: vi.fn(),
  retireKnowledgeDocument: vi.fn(),
  retryKnowledgeIndex: vi.fn(),
  retryKnowledgeVectorCleanup: vi.fn(),
  searchKnowledge: vi.fn(),
  uploadKnowledgeDocument: vi.fn(),
}))

const mockedGetDocument = vi.mocked(getKnowledgeDocument)
const mockedListDocuments = vi.mocked(listKnowledgeDocuments)
const mockedListJobs = vi.mocked(listKnowledgeIndexJobs)
const mockedListEvents = vi.mocked(listKnowledgeDocumentEvents)
const mockedPublish = vi.mocked(publishKnowledgeDocument)
const mockedUpload = vi.mocked(uploadKnowledgeDocument)
const mockedSearch = vi.mocked(searchKnowledge)

function documentFixture(
  overrides: Partial<KnowledgeDocument> = {},
): KnowledgeDocument {
  return {
    document_id: 'knowledge-doc-1',
    knowledge_space: 'access_and_security',
    title: '远程访问管理制度',
    source_uri: 'policy://remote-access',
    version_label: '2026.1',
    source_department: '信息安全部',
    trust_level: 'AUTHORITATIVE',
    effective_from: '2026-07-19T08:00:00Z',
    effective_until: null,
    allowed_roles: ['employee', 'approver'],
    allowed_user_ids: [],
    publication_status: 'DRAFT',
    index_status: 'INDEXED',
    index_version: 'te4-d1024-c800-o120-v1',
    chunk_count: 1,
    published_at: null,
    published_by: null,
    retired_at: null,
    retired_by: null,
    supersedes_document_id: null,
    original_filename: 'remote-access.md',
    source_format: 'MARKDOWN',
    source_size_bytes: 128,
    created_at: '2026-07-19T08:00:00Z',
    updated_at: '2026-07-19T08:02:00Z',
    ...overrides,
  }
}

function jobFixture(
  overrides: Partial<KnowledgeIndexJob> = {},
): KnowledgeIndexJob {
  return {
    job_id: 'index-job-1',
    document_id: 'knowledge-doc-1',
    index_version: 'te4-d1024-c800-o120-v1',
    status: 'SUCCEEDED',
    attempt_count: 1,
    max_attempts: 3,
    available_at: '2026-07-19T08:00:00Z',
    lease_owner: null,
    lease_expires_at: null,
    last_error_code: null,
    last_error_message: null,
    started_at: '2026-07-19T08:01:00Z',
    finished_at: '2026-07-19T08:02:00Z',
    created_at: '2026-07-19T08:00:00Z',
    updated_at: '2026-07-19T08:02:00Z',
    ...overrides,
  }
}

const eventFixture: KnowledgeDocumentEvent = {
  event_id: 'event-1',
  document_id: 'knowledge-doc-1',
  event_type: 'INDEXING_SUCCEEDED',
  actor_id: 'knowledge-worker',
  actor_roles: ['system'],
  safe_payload: { attempt_count: 1 },
  created_at: '2026-07-19T08:02:00Z',
}

function detailFixture(
  document: KnowledgeDocument = documentFixture(),
  job: KnowledgeIndexJob = jobFixture(),
): KnowledgeDocumentDetail {
  return {
    document,
    latest_index_job: job,
    chunks: [
      {
        chunk_id: 'chunk-1',
        chunk_index: 0,
        char_count: 520,
        excerpt: '远程访问必须启用多因素认证，并经过直属领导审批。',
      },
    ],
  }
}

function renderPage(initialEntry = '/knowledge/knowledge-doc-1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/knowledge/:documentId" element={<KnowledgePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function setDefaultMocks(detail = detailFixture()) {
  mockedListDocuments.mockResolvedValue({
    items: [detail.document],
    page: 1,
    page_size: 10,
    total: 1,
    total_pages: 1,
  })
  mockedGetDocument.mockResolvedValue(detail)
  mockedListJobs.mockResolvedValue({
    items: detail.latest_index_job ? [detail.latest_index_job] : [],
    page: 1,
    page_size: 20,
    total: detail.latest_index_job ? 1 : 0,
    total_pages: detail.latest_index_job ? 1 : 0,
  })
  mockedListEvents.mockResolvedValue({
    items: [eventFixture],
    page: 1,
    page_size: 50,
    total: 1,
    total_pages: 1,
  })
}

describe('KnowledgePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({
      user: {
        employee_id: 'EMP-KNOWLEDGE-OPERATOR',
        username: 'operator',
        roles: ['operator'],
      },
      initialized: true,
    })
    vi.mocked(retireKnowledgeDocument).mockResolvedValue({
      document: documentFixture({ publication_status: 'RETIRED' }),
      retired_document_ids: ['knowledge-doc-1'],
      vector_cleanup_failed_document_ids: [],
    })
    vi.mocked(retryKnowledgeIndex).mockResolvedValue(jobFixture({ status: 'PENDING' }))
    vi.mocked(retryKnowledgeVectorCleanup).mockResolvedValue({
      document: documentFixture({ publication_status: 'RETIRED' }),
      retired_document_ids: ['knowledge-doc-1'],
      vector_cleanup_failed_document_ids: [],
    })
  })

  it('shows localized governance tabs and keeps safe excerpts collapsed', async () => {
    setDefaultMocks()
    renderPage()

    expect((await screen.findAllByText('远程访问管理制度')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('草稿').length).toBeGreaterThan(0)
    expect(screen.getAllByText('索引完成').length).toBeGreaterThan(0)
    expect(screen.getAllByText('权限与信息安全').length).toBeGreaterThan(0)
    expect(screen.queryByText('access_and_security')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '审计轨迹 (1)' }))
    expect(screen.getByText('知识索引建立成功')).toBeInTheDocument()
    expect(screen.queryByText('INDEXING_SUCCEEDED')).not.toBeInTheDocument()
    expect(screen.queryByText('DRAFT')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '索引与片段 (1)' }))
    await screen.findByText('默认折叠，不展示完整原文')
    const excerpt = document.querySelector('.knowledge-chunk-list details')
    expect(excerpt).not.toHaveAttribute('open')

    fireEvent.mouseDown(screen.getByLabelText('文档类别筛选'))
    fireEvent.click(await screen.findByText('工业设备维修'))
    await waitFor(() =>
      expect(mockedListDocuments).toHaveBeenLastCalledWith(
        expect.objectContaining({ knowledge_space: 'equipment_maintenance' }),
      ),
    )
  })

  it('keeps the catalog wide and fetches details only after selecting a document', async () => {
    setDefaultMocks()
    renderPage('/knowledge')

    expect(await screen.findByText('共 1 份')).toBeInTheDocument()
    expect(
      screen.getByLabelText('文档类别筛选').closest('.ant-select'),
    ).toHaveTextContent('文档类别')
    expect(mockedGetDocument).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '查看详情' }))

    expect(await screen.findByText('生命周期操作')).toBeInTheDocument()
    expect(mockedGetDocument).toHaveBeenCalledWith('knowledge-doc-1')
  })

  it('confirms publication and delegates the state change to the API', async () => {
    setDefaultMocks()
    mockedPublish.mockResolvedValue({
      document: documentFixture({ publication_status: 'PUBLISHED' }),
      retired_document_ids: [],
      vector_cleanup_failed_document_ids: [],
    })
    renderPage()

    await screen.findByText('生命周期操作')
    fireEvent.click(screen.getByRole('button', { name: /发\s*布/ }))
    expect(await screen.findByText('确认发布知识文档')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }))

    await waitFor(() =>
      expect(mockedPublish).toHaveBeenCalledWith('knowledge-doc-1'),
    )
  })

  it('keeps retirement confirmation above the document detail and executes it', async () => {
    setDefaultMocks()
    renderPage()

    await screen.findByText('生命周期操作')
    fireEvent.click(screen.getByRole('button', { name: /下\s*线/ }))

    expect(await screen.findByText('确认下线知识文档')).toBeInTheDocument()
    const confirmRoot = document.querySelector(
      '.knowledge-action-confirm-root',
    )
    expect(confirmRoot).toBeInTheDocument()
    expect(confirmRoot?.querySelector('.ant-modal-wrap')).toHaveStyle({
      zIndex: '1300',
    })

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }))
    await waitFor(() =>
      expect(retireKnowledgeDocument).toHaveBeenCalledWith('knowledge-doc-1'),
    )
  })

  it('submits a structured file upload and never asks for a storage path', async () => {
    setDefaultMocks()
    const uploaded = documentFixture({ document_id: 'knowledge-doc-2' })
    mockedUpload.mockResolvedValue({
      document: uploaded,
      index_job: jobFixture({
        job_id: 'index-job-2',
        document_id: 'knowledge-doc-2',
        status: 'PENDING',
      }),
    })
    renderPage('/knowledge')

    fireEvent.click(await screen.findByRole('button', { name: '上传文档' }))
    const file = new File(['# VPN'], 'vpn.md', { type: 'text/markdown' })
    fireEvent.change(screen.getByLabelText('知识文件'), {
      target: { files: [file] },
    })
    fireEvent.change(screen.getByLabelText(/文档标题/), {
      target: { value: 'VPN管理制度' },
    })
    fireEvent.change(screen.getByLabelText('来源部门 *', { exact: true }), {
      target: { value: '信息安全部' },
    })
    fireEvent.change(screen.getByLabelText(/来源标识/), {
      target: { value: 'policy://vpn-new' },
    })
    fireEvent.click(screen.getByRole('button', { name: '登记并创建索引任务' }))

    await waitFor(() => expect(mockedUpload).toHaveBeenCalledOnce())
    expect(mockedUpload.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        file,
        title: 'VPN管理制度',
        source_uri: 'policy://vpn-new',
        source_department: '信息安全部',
      }),
    )
    expect(screen.queryByText(/storage_key/i)).not.toBeInTheDocument()
  })

  it('polls a pending durable job until the indexed state is visible', async () => {
    const pendingDocument = documentFixture({ index_status: 'PENDING' })
    const pendingDetail = detailFixture(
      pendingDocument,
      jobFixture({ status: 'PENDING', attempt_count: 0 }),
    )
    const indexedDetail = detailFixture()
    setDefaultMocks(pendingDetail)
    mockedGetDocument
      .mockResolvedValueOnce(pendingDetail)
      .mockResolvedValue(indexedDetail)

    renderPage()

    expect(await screen.findByText('正在自动刷新索引状态')).toBeInTheDocument()
    expect(
      await screen.findByText('索引完成', {}, { timeout: 4500 }),
    ).toBeInTheDocument()
    expect(mockedGetDocument.mock.calls.length).toBeGreaterThanOrEqual(2)
  }, 6000)

  it('tests retrieval with the current identity and renders safe citations collapsed', async () => {
    setDefaultMocks(detailFixture(documentFixture({ publication_status: 'PUBLISHED' })))
    mockedSearch.mockResolvedValue({
      retrieval_id: 'retrieval-1',
      knowledge_space: 'access_and_security',
      query: 'VPN需要谁审批？',
      citations: [{
        document_id: 'knowledge-doc-1',
        chunk_id: 'chunk-1',
        chunk_index: 0,
        title: '远程访问管理制度',
        source_uri: 'policy://remote-access',
        version_label: '2026.1',
        source_department: '信息安全部',
        trust_level: 'AUTHORITATIVE',
        excerpt: 'VPN远程访问必须经过直属领导审批。',
        vector_score: 0.9,
        lexical_score: 0.8,
        combined_score: 0.87,
      }],
    })
    renderPage('/knowledge')

    fireEvent.click(await screen.findByRole('button', { name: '测试检索' }))
    expect(screen.getByText('当前检索账号：operator')).toBeInTheDocument()
    expect(screen.getByText('知识运营人员')).toBeInTheDocument()
    expect(screen.getByText(/不接受员工编号或角色参数/)).toBeInTheDocument()
    expect(screen.getByLabelText('测试问题')).toHaveValue(
      'VPN远程访问需要经过谁审批？',
    )
    fireEvent.click(screen.getByRole('button', { name: '开始检索' }))

    await waitFor(() => expect(mockedSearch).toHaveBeenCalledWith({
      query: 'VPN远程访问需要经过谁审批？',
      knowledge_space: 'access_and_security',
      top_k: 5,
    }))
    expect(await screen.findByText('找到 1 个可引用片段')).toBeInTheDocument()
    const searchDetails = Array.from(document.querySelectorAll('.knowledge-search-results details'))
    expect(searchDetails).toHaveLength(1)
    expect(searchDetails[0]).not.toHaveAttribute('open')
    expect(screen.queryByText(/actor_id/i)).not.toBeInTheDocument()
  })

  it('changes the default retrieval question with the document category', async () => {
    setDefaultMocks()
    renderPage('/knowledge')

    fireEvent.click(await screen.findByRole('button', { name: '测试检索' }))
    expect(screen.getByLabelText('测试问题')).toHaveValue(
      'VPN远程访问需要经过谁审批？',
    )

    fireEvent.mouseDown(screen.getByLabelText('测试文档类别'))
    fireEvent.click(await screen.findByText('工业设备维修'))

    expect(screen.getByLabelText('测试问题')).toHaveValue(
      '冲压设备异常振动时应该如何排查和报修？',
    )
  })

  it('does not reveal filtered document metadata when current identity has no result', async () => {
    setDefaultMocks()
    mockedSearch.mockResolvedValue({
      retrieval_id: 'retrieval-empty',
      knowledge_space: 'access_and_security',
      query: '管理员专用应急授权',
      citations: [],
    })
    renderPage('/knowledge')

    fireEvent.click(await screen.findByRole('button', { name: '测试检索' }))
    fireEvent.change(screen.getByLabelText('测试问题'), {
      target: { value: '管理员专用应急授权' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始检索' }))

    expect(await screen.findByText(/当前身份没有检索到可引用的已发布知识/)).toBeInTheDocument()
    expect(screen.queryByText('管理员应急授权秘密制度')).not.toBeInTheDocument()
  })
})
