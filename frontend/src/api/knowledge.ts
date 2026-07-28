import { apiClient } from './client'
import type {
  KnowledgeDocumentDetail,
  KnowledgeDocumentEventPage,
  KnowledgeDocumentPage,
  KnowledgeIndexJob,
  KnowledgeIndexJobPage,
  KnowledgeIndexStatus,
  KnowledgeLifecycleResult,
  KnowledgePublicationStatus,
  KnowledgeSearchResult,
  KnowledgeTrustLevel,
  KnowledgeUploadResult,
} from './contracts'

export const KNOWLEDGE_UPLOAD_TIMEOUT_MS = 120_000

export interface KnowledgeListFilters {
  knowledge_space?: string
  publication_status?: KnowledgePublicationStatus
  index_status?: KnowledgeIndexStatus
  source_department?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface KnowledgeUploadInput {
  file: File
  knowledge_space: string
  title: string
  source_uri: string
  version_label: string
  source_department: string
  trust_level: KnowledgeTrustLevel
  effective_from?: string
  effective_until?: string
  allowed_roles: string[]
  allowed_user_ids: string[]
}

export interface KnowledgeSearchInput {
  query: string
  knowledge_space: string
  top_k: number
}

export async function listKnowledgeDocuments(
  filters: KnowledgeListFilters,
): Promise<KnowledgeDocumentPage> {
  const { data } = await apiClient.get<KnowledgeDocumentPage>(
    '/knowledge/documents',
    { params: filters },
  )
  return data
}

export async function getKnowledgeDocument(
  documentId: string,
): Promise<KnowledgeDocumentDetail> {
  const { data } = await apiClient.get<KnowledgeDocumentDetail>(
    `/knowledge/documents/${documentId}`,
  )
  return data
}

export async function listKnowledgeIndexJobs(
  documentId: string,
): Promise<KnowledgeIndexJobPage> {
  const { data } = await apiClient.get<KnowledgeIndexJobPage>(
    '/knowledge/index-jobs',
    { params: { document_id: documentId, page: 1, page_size: 20 } },
  )
  return data
}

export async function listKnowledgeDocumentEvents(
  documentId: string,
): Promise<KnowledgeDocumentEventPage> {
  const { data } = await apiClient.get<KnowledgeDocumentEventPage>(
    `/knowledge/documents/${documentId}/events`,
    { params: { page: 1, page_size: 50 } },
  )
  return data
}

export async function uploadKnowledgeDocument(
  input: KnowledgeUploadInput,
): Promise<KnowledgeUploadResult> {
  const form = new FormData()
  form.append('file', input.file)
  form.append('knowledge_space', input.knowledge_space.trim())
  form.append('title', input.title.trim())
  form.append('source_uri', input.source_uri.trim())
  form.append('version_label', input.version_label.trim())
  form.append('source_department', input.source_department.trim())
  form.append('trust_level', input.trust_level)
  if (input.effective_from) form.append('effective_from', input.effective_from)
  if (input.effective_until) form.append('effective_until', input.effective_until)
  input.allowed_roles.forEach((role) => form.append('allowed_roles', role))
  input.allowed_user_ids.forEach((userId) =>
    form.append('allowed_user_ids', userId),
  )
  const { data } = await apiClient.post<KnowledgeUploadResult>(
    '/knowledge/documents/upload',
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: KNOWLEDGE_UPLOAD_TIMEOUT_MS,
    },
  )
  return data
}

export async function retryKnowledgeIndex(
  documentId: string,
): Promise<KnowledgeIndexJob> {
  const { data } = await apiClient.post<KnowledgeIndexJob>(
    `/knowledge/documents/${documentId}/index`,
  )
  return data
}

export async function publishKnowledgeDocument(
  documentId: string,
): Promise<KnowledgeLifecycleResult> {
  const { data } = await apiClient.post<KnowledgeLifecycleResult>(
    `/knowledge/documents/${documentId}/publish`,
  )
  return data
}

export async function retireKnowledgeDocument(
  documentId: string,
): Promise<KnowledgeLifecycleResult> {
  const { data } = await apiClient.post<KnowledgeLifecycleResult>(
    `/knowledge/documents/${documentId}/retire`,
  )
  return data
}

export async function retryKnowledgeVectorCleanup(
  documentId: string,
): Promise<KnowledgeLifecycleResult> {
  const { data } = await apiClient.post<KnowledgeLifecycleResult>(
    `/knowledge/documents/${documentId}/vector-cleanup`,
  )
  return data
}

export async function searchKnowledge(
  input: KnowledgeSearchInput,
): Promise<KnowledgeSearchResult> {
  const { data } = await apiClient.post<KnowledgeSearchResult>(
    '/knowledge/search',
    {
      query: input.query.trim(),
      knowledge_space: input.knowledge_space.trim(),
      top_k: input.top_k,
    },
  )
  return data
}
