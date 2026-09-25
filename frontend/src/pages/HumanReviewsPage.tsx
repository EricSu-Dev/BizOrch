import { useEffect, useState } from 'react'
import { Button, Empty, Input, List, Select, Space, Typography, message as toast } from 'antd'

import { publicErrorMessage } from '../api/client'
import {
  decideHumanReview,
  listHumanReviews,
  type HumanReviewItem,
  type HumanReviewOutcome,
} from '../api/humanReviews'
import { scenarioLabel } from '../presentation/scenarios'

const outcomes: { value: HumanReviewOutcome; label: string }[] = [
  { value: 'NOTE', label: '记录进展，继续人工核对' },
  { value: 'CONFIRMED_SUCCESS', label: '人工核实已生效，结案' },
  { value: 'CONFIRMED_FAILURE', label: '人工核实未完成，结案' },
  { value: 'CANCELLED', label: '人工确认取消，结案' },
]

export function HumanReviewsPage() {
  const [items, setItems] = useState<HumanReviewItem[]>([])
  const [selected, setSelected] = useState<HumanReviewItem | null>(null)
  const [outcome, setOutcome] = useState<HumanReviewOutcome>('NOTE')
  const [summary, setSummary] = useState('')
  const [evidence, setEvidence] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  const refresh = async () => {
    const next = await listHumanReviews()
    setItems(next)
    setSelected((current) => next.find((item) => item.workflow_run_id === current?.workflow_run_id) || null)
  }

  useEffect(() => {
    void refresh()
      .catch((error) => toast.error(publicErrorMessage(error)))
      .finally(() => setLoading(false))
  }, [])

  const submit = async () => {
    if (!selected || summary.trim().length < 10 || (outcome !== 'NOTE' && !evidence.trim())) {
      toast.warning('请填写至少 10 字的公开处理说明；结案还需要证据编号。')
      return
    }
    setSubmitting(true)
    try {
      await decideHumanReview(selected.workflow_run_id, {
        expected_workflow_version: selected.version,
        outcome,
        public_summary: summary.trim(),
        evidence_reference: evidence.trim() || undefined,
      })
      toast.success(outcome === 'NOTE' ? '处理进展已记录' : '人工核对已结案')
      setSummary('')
      setEvidence('')
      await refresh()
    } catch (error) {
      toast.error(publicErrorMessage(error))
      await refresh().catch(() => {})
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="human-reviews-page">
      <Typography.Title level={2}>人工核对工作台</Typography.Title>
      <Typography.Paragraph>仅处理已安全暂停的流程。结案不会重试企业写操作。确认成功只适用于存在结果不确定执行记录的单操作流程；组合计划需逐步核对，暂不能整体确认成功。</Typography.Paragraph>
      <Space align="start" size="large" wrap>
        <section aria-label="待核对流程" style={{ minWidth: 320, maxWidth: 520 }}>
          <Typography.Title level={4}>待核对流程</Typography.Title>
          <List
            loading={loading}
            dataSource={items}
            locale={{ emptyText: <Empty description="当前没有待核对流程" /> }}
            renderItem={(item) => (
              <List.Item>
                <Button type={selected?.workflow_run_id === item.workflow_run_id ? 'primary' : 'link'} onClick={() => { setSelected(item); setOutcome('NOTE') }}>
                  {scenarioLabel(item.scenario_key)} · {item.title} · V{item.version}
                </Button>
              </List.Item>
            )}
          />
        </section>
        {selected && (
          <section aria-label="人工核对决定" style={{ minWidth: 320, maxWidth: 520 }}>
            <Typography.Title level={4}>记录核对结果</Typography.Title>
            <Typography.Paragraph>流程 {selected.workflow_run_id} · 申请人 {selected.requester_id}</Typography.Paragraph>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Select aria-label="处理结果" value={outcome} options={outcomes.filter((item) => item.value !== 'CONFIRMED_SUCCESS' || selected.can_confirm_success)} onChange={setOutcome} />
              <Input.TextArea aria-label="公开处理说明" value={summary} maxLength={1000} rows={4} placeholder="说明核对结果。该文字会展示给申请人，请勿填写密码或令牌。" onChange={(event) => setSummary(event.target.value)} />
              <Input aria-label="证据编号" value={evidence} maxLength={200} placeholder="结案时填写外部工单或核对记录编号" onChange={(event) => setEvidence(event.target.value)} />
              <Button type="primary" loading={submitting} onClick={() => void submit()}>提交处理结果</Button>
            </Space>
          </section>
        )}
      </Space>
    </div>
  )
}
