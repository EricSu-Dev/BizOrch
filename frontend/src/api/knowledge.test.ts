import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from './client'
import {
  KNOWLEDGE_UPLOAD_TIMEOUT_MS,
  listKnowledgeDocuments,
  searchKnowledge,
  uploadKnowledgeDocument,
} from './knowledge'

vi.mock('./client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const mockedGet = vi.mocked(apiClient.get)
const mockedPost = vi.mocked(apiClient.post)

describe('knowledge API client', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('passes pagination and governance filters to the document endpoint', async () => {
    mockedGet.mockResolvedValue({
      data: { items: [], page: 2, page_size: 10, total: 0, total_pages: 0 },
    })

    await listKnowledgeDocuments({
      page: 2,
      page_size: 10,
      keyword: 'VPN',
      publication_status: 'DRAFT',
      index_status: 'FAILED',
    })

    expect(mockedGet).toHaveBeenCalledWith('/knowledge/documents', {
      params: {
        page: 2,
        page_size: 10,
        keyword: 'VPN',
        publication_status: 'DRAFT',
        index_status: 'FAILED',
      },
    })
  })

  it('builds a multipart upload without server paths or storage keys', async () => {
    mockedPost.mockResolvedValue({ data: { document: {}, index_job: {} } })
    const file = new File(['# VPN'], 'vpn-policy.md', { type: 'text/markdown' })

    await uploadKnowledgeDocument({
      file,
      knowledge_space: 'access_and_security',
      title: 'VPN 制度',
      source_uri: 'policy://vpn',
      version_label: '2026.2',
      source_department: '信息安全部',
      trust_level: 'AUTHORITATIVE',
      allowed_roles: ['employee', 'approver'],
      allowed_user_ids: ['EMP-1001'],
    })

    expect(mockedPost).toHaveBeenCalledOnce()
    const [path, body, config] = mockedPost.mock.calls[0]
    expect(path).toBe('/knowledge/documents/upload')
    expect(body).toBeInstanceOf(FormData)
    const form = body as FormData
    expect(form.get('file')).toBe(file)
    expect(form.get('source_uri')).toBe('policy://vpn')
    expect(form.getAll('allowed_roles')).toEqual(['employee', 'approver'])
    expect(form.has('storage_key')).toBe(false)
    expect(config).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: KNOWLEDGE_UPLOAD_TIMEOUT_MS,
    })
  })

  it('searches with only query, space and top-k so identity cannot be impersonated', async () => {
    mockedPost.mockResolvedValue({
      data: {
        retrieval_id: 'retrieval-1',
        knowledge_space: 'access_and_security',
        query: 'VPN审批',
        citations: [],
      },
    })

    await searchKnowledge({
      query: '  VPN审批  ',
      knowledge_space: ' access_and_security ',
      top_k: 5,
    })

    expect(mockedPost).toHaveBeenCalledWith('/knowledge/search', {
      query: 'VPN审批',
      knowledge_space: 'access_and_security',
      top_k: 5,
    })
    const body = mockedPost.mock.calls[0][1] as Record<string, unknown>
    expect(body).not.toHaveProperty('actor_id')
    expect(body).not.toHaveProperty('actor_roles')
  })
})
