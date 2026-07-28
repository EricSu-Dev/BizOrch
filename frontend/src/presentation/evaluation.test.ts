import { describe, expect, it } from 'vitest'

import { formatEvaluationCaseName, formatEvaluationSuiteName } from './evaluation'

describe('evaluation presentation mappings', () => {
  it('renders fixed suite and case identifiers as Chinese business labels', () => {
    expect(formatEvaluationSuiteName('v5_procurement', 'ignored')).toBe('采购与办公申请评测')
    expect(formatEvaluationSuiteName('v61_procurement_challenge', 'ignored')).toBe('采购智能体与知识检索挑战评测')
    expect(formatEvaluationCaseName('safety_maintenance_reject_action_type')).toBe('拒绝注入维修写操作类型')
    expect(formatEvaluationCaseName('v61-plan-injection-and-identity')).toBe('阻止采购注入与身份伪造')
  })

  it('keeps an understandable fallback for a future server-side case id', () => {
    expect(formatEvaluationCaseName('future_case')).toBe('评测用例：future_case')
  })
})
