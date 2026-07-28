import axios from 'axios'

import { getAccessToken } from './tokenStorage'

export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(
  /\/$/,
  '',
)

export const apiClient = axios.create({
  baseURL: apiBaseUrl,
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export function isRequestTimeout(error: unknown): boolean {
  return axios.isAxiosError(error) && error.code === 'ECONNABORTED'
}

export function publicErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const code = error.response?.data?.error?.code
    const knowledgeMessages: Record<string, string> = {
      KNOWLEDGE_VERSION_CONFLICT: '同一来源和版本已经存在，请修改版本号后重试。',
      KNOWLEDGE_LIFECYCLE_CONFLICT: '当前文档状态不允许执行此操作，请刷新后重试。',
      KNOWLEDGE_INDEX_RETRY_NOT_ALLOWED: '当前文档不符合重新索引条件。',
      KNOWLEDGE_FILE_UNSUPPORTED: '仅支持 Markdown、TXT 和文本型 PDF 文件。',
      KNOWLEDGE_FILE_INVALID: '文件内容无效、格式不符或无法安全解析。',
      KNOWLEDGE_FILE_TOO_LARGE: '文件超过知识库允许的大小限制。',
      KNOWLEDGE_UPLOAD_UNAVAILABLE: '知识文件上传服务尚未正确配置。',
      KNOWLEDGE_FILE_STORAGE_FAILED: '知识文件暂时无法安全保存，请稍后重试。',
      KNOWLEDGE_SERVICE_UNAVAILABLE: '知识检索或向量服务尚未正确配置。',
      KNOWLEDGE_INDEXING_FAILED: '知识索引建立失败，请检查任务状态后重试。',
      KNOWLEDGE_RETRIEVAL_FAILED: '知识检索暂时失败，请稍后重试。',
      RESOURCE_NOT_FOUND: '请求的知识文档或索引任务不存在。',
      ACTION_FORBIDDEN: '当前账号没有执行此操作的权限。',
      USER_CONFLICT: '该登录账号名已被使用，请更换后重试。',
      CURRENT_PASSWORD_INVALID: '当前密码不正确，请重新输入。',
      PASSWORD_REUSE: '新密码不能与当前密码相同。',
      AVATAR_FILE_UNSUPPORTED: '头像仅支持 PNG、JPEG 和 WebP 图片。',
      AVATAR_FILE_TOO_LARGE: '头像图片不能超过 1MB。',
      EVALUATION_COMMAND_CONFLICT: '相同命令键对应的评测请求不一致，请刷新后重试。',
      EVALUATION_GOVERNANCE_CONFLICT: '当前评测状态或版本已变化，请刷新后再处理。',
      EVALUATION_GOVERNANCE_VALIDATION: '当前评测结果不满足该治理操作的前提条件。',
      EVALUATION_NOT_FOUND: '请求的评测运行、基线或 Bad Case 不存在。',
      VALIDATION_ERROR: '提交的信息不完整或格式不正确。',
    }
    if (typeof code === 'string' && knowledgeMessages[code]) {
      return knowledgeMessages[code]
    }
    const message = error.response?.data?.error?.message
    if (typeof message === 'string' && message.length > 0) {
      return message
    }
    if (isRequestTimeout(error)) {
      return '请求超时，请稍后重试。'
    }
  }
  return '服务暂时不可用，请稍后重试。'
}
