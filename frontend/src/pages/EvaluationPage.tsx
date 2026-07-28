import { useCallback, useEffect, useState } from 'react'
import { Button, Checkbox, Empty, Input, Modal, Radio, Select, Skeleton, Table, Tabs, Tag, message as toast } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { publicErrorMessage } from '../api/client'
import type { EvaluationBadCase, EvaluationCaseResult, EvaluationRun, EvaluationRunMode, EvaluationSuite } from '../api/contracts'
import { archiveEvaluationBadCase, closeEvaluationBadCase, createEvaluationRetest, createEvaluationRun, getEvaluationComparison, getEvaluationRun, listEvaluationBadCases, listEvaluationBaselines, listEvaluationCases, listEvaluationRuns, listEvaluationSuites, setEvaluationBaseline, updateEvaluationBadCase, verifyEvaluationBadCase } from '../api/evaluations'
import { evaluationBadCaseStatusView, evaluationCaseStatusView, evaluationCategoryView, evaluationRunModeView, evaluationRunStatusView, evaluationSeverityView, formatEvaluationCaseName, formatEvaluationDate, formatEvaluationSuiteName, formatRate, newClientCommandKey } from '../presentation/evaluation'

function RunTag({ run }: { run: EvaluationRun }) {
  const item = evaluationRunStatusView[run.status]
  return <Tag color={item.color}>{item.label}</Tag>
}

function BadCaseCard({ item, reload }: { item: EvaluationBadCase; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [assignee, setAssignee] = useState(item.assignee_id || '')
  const [note, setNote] = useState(item.remediation_note || '')
  const [targetVersion, setTargetVersion] = useState(item.target_fix_version || '')
  const act = async (command: () => Promise<unknown>, success: string) => {
    setBusy(true)
    try { await command(); toast.success(success); await reload() } catch (error) { toast.error(publicErrorMessage(error)) } finally { setBusy(false) }
  }
  const status = evaluationBadCaseStatusView[item.status]
  const severity = evaluationSeverityView[item.severity]
  return <article className="evaluation-bad-case-card">
    <div className="evaluation-card-topline"><div><Tag color={severity.color}>{severity.label}风险</Tag><Tag color={status.color}>{status.label}</Tag></div><small>{formatEvaluationDate(item.updated_at)}</small></div>
    <h3>{item.safe_issue_summary}</h3><p>{evaluationCategoryView[item.category]} · 用例：{formatEvaluationCaseName(item.case_id)}</p>
    <dl className="evaluation-mini-facts"><div><dt>处理人</dt><dd>{item.assignee_id || '尚未指派'}</dd></div><div><dt>修复版本</dt><dd>{item.target_fix_version || '尚未填写'}</dd></div><div><dt>复测状态</dt><dd>{item.latest_retest_status || '尚未创建'}</dd></div></dl>
    {item.remediation_note && <p className="evaluation-remediation-note">修复说明：{item.remediation_note}</p>}
    {item.status === 'OPEN' && <div className="evaluation-inline-action"><Input value={assignee} onChange={(event) => setAssignee(event.target.value)} placeholder="处理人账号，例如 operator" /><Button loading={busy} onClick={() => void act(() => updateEvaluationBadCase(item.bad_case_id, { action: 'START_WORK', expected_version: item.version, assignee_id: assignee.trim() }), 'Bad Case 已进入处理中')}>开始处理</Button></div>}
    {item.status === 'IN_PROGRESS' && <div className="evaluation-remediation-form"><Input value={targetVersion} onChange={(event) => setTargetVersion(event.target.value)} placeholder="修复版本，例如 2026.2" /><Input.TextArea value={note} onChange={(event) => setNote(event.target.value)} placeholder="脱敏的修复说明" rows={2} /><Button loading={busy} onClick={() => void act(() => updateEvaluationBadCase(item.bad_case_id, { action: 'READY_FOR_RETEST', expected_version: item.version, remediation_note: note.trim(), target_fix_version: targetVersion.trim() }), 'Bad Case 已标记为待复测')}>提交复测准备</Button></div>}
    {item.status === 'READY_FOR_RETEST' && item.latest_retest_status !== 'COMPLETED' && <Button loading={busy} onClick={() => void act(() => createEvaluationRetest(item.bad_case_id, item.version, newClientCommandKey()), '复测运行已创建，等待评测 Worker 执行')}>创建复测运行</Button>}
    {item.status === 'READY_FOR_RETEST' && item.latest_retest_status === 'COMPLETED' && <Button loading={busy} onClick={() => void act(() => verifyEvaluationBadCase(item.bad_case_id, item.version), '复测证据已验证')}>验证复测结果</Button>}
    {item.status === 'VERIFIED' && <Button loading={busy} onClick={() => void act(() => closeEvaluationBadCase(item.bad_case_id, item.version), 'Bad Case 已关闭')}>关闭 Bad Case</Button>}
  </article>
}

export function EvaluationPage() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [suites, setSuites] = useState<EvaluationSuite[]>([])
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [badCases, setBadCases] = useState<EvaluationBadCase[]>([])
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null)
  const [cases, setCases] = useState<EvaluationCaseResult[]>([])
  const [comparison, setComparison] = useState<Awaited<ReturnType<typeof getEvaluationComparison>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [suiteKey, setSuiteKey] = useState('')
  const [mode, setMode] = useState<EvaluationRunMode>('CONTRACT_ONLY')
  const [confirmLive, setConfirmLive] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState<EvaluationCaseResult | null>(null)
  const [archiveSummary, setArchiveSummary] = useState('')

  const reloadBadCases = useCallback(async () => { setBadCases((await listEvaluationBadCases()).items) }, [])
  const loadOverview = useCallback(async () => {
    setLoading(true)
    try {
      const [suitePage, runPage, badCasePage, baselinePage] = await Promise.all([listEvaluationSuites(), listEvaluationRuns(), listEvaluationBadCases(), listEvaluationBaselines()])
      setSuites(suitePage.items); setRuns(runPage.items); setBadCases(badCasePage.items)
      setBaselineRunId(baselinePage.items.find((item) => item.is_current)?.run_id || null)
      setSuiteKey((value) => value || suitePage.items[0]?.suite_key || '')
    } catch (error) { toast.error(publicErrorMessage(error)) } finally { setLoading(false) }
  }, [])
  const loadDetail = useCallback(async (id: string) => {
    try {
      const [run, resultPage] = await Promise.all([getEvaluationRun(id), listEvaluationCases(id)])
      setSelectedRun(run); setCases(resultPage.items)
      if (run.status === 'COMPLETED') { try { setComparison(await getEvaluationComparison(id)) } catch { setComparison(null) } } else setComparison(null)
    } catch (error) { toast.error(publicErrorMessage(error)); navigate('/evaluations', { replace: true }) }
  }, [navigate])
  useEffect(() => { void loadOverview() }, [loadOverview])
  useEffect(() => { if (runId) void loadDetail(runId); else { setSelectedRun(null); setCases([]); setComparison(null) } }, [loadDetail, runId])
  useEffect(() => {
    if (selectedRun?.status !== 'PENDING' && selectedRun?.status !== 'RUNNING') return
    const timer = window.setInterval(() => { void loadOverview(); if (runId) void loadDetail(runId) }, 2000)
    return () => window.clearInterval(timer)
  }, [loadDetail, loadOverview, runId, selectedRun?.status])

  const createRun = async () => {
    if (!suiteKey || (mode === 'LIVE_READ_ONLY' && !confirmLive)) return
    setSubmitting(true)
    try {
      const run = await createEvaluationRun({ suite_key: suiteKey, mode, confirm_live_external_calls: confirmLive }, newClientCommandKey())
      toast.success('评测运行已创建，等待 Worker 领取'); setCreateOpen(false); setMode('CONTRACT_ONLY'); setConfirmLive(false)
      await loadOverview(); navigate(`/evaluations/${run.run_id}`)
    } catch (error) { toast.error(publicErrorMessage(error)) } finally { setSubmitting(false) }
  }
  const archive = async () => {
    if (!archiveTarget || !archiveSummary.trim()) return
    try { await archiveEvaluationBadCase(archiveTarget.run_id, archiveTarget.case_id, { severity: 'MEDIUM', safe_issue_summary: archiveSummary.trim() }); toast.success('Bad Case 已归档'); setArchiveTarget(null); setArchiveSummary(''); await reloadBadCases() } catch (error) { toast.error(publicErrorMessage(error)) }
  }
  const setBaseline = () => selectedRun && Modal.confirm({ title: '确认设为当前基线', content: '只有已完成的兼容结果可以成为基线。切换后历史基线仍会保留。', okText: '确认设置', cancelText: '取消', onOk: async () => { try { await setEvaluationBaseline(selectedRun.run_id); toast.success('已设为当前基线'); await loadOverview() } catch (error) { toast.error(publicErrorMessage(error)) } } })
  const suite = suites.find((item) => item.suite_key === suiteKey)
  const runColumns = [
    { title: '评测套件', render: (_: unknown, item: EvaluationRun) => <><strong>{formatEvaluationSuiteName(item.suite_key, item.suite_name)}</strong><small className="table-subtitle">{item.suite_version}</small></> },
    { title: '模式', render: (_: unknown, item: EvaluationRun) => <Tag color={evaluationRunModeView[item.mode].color}>{evaluationRunModeView[item.mode].label}</Tag> },
    { title: '状态', render: (_: unknown, item: EvaluationRun) => <RunTag run={item} /> },
    { title: '适用通过率', render: (_: unknown, item: EvaluationRun) => formatRate(item.pass_rate) },
    { title: '进度', render: (_: unknown, item: EvaluationRun) => `${item.completed_case_count} / ${item.total_case_count}` },
    { title: '创建时间', render: (_: unknown, item: EvaluationRun) => formatEvaluationDate(item.created_at) },
    { title: '', render: (_: unknown, item: EvaluationRun) => <Button type="link" onClick={() => navigate(`/evaluations/${item.run_id}`)}>查看</Button> },
  ]
  if (loading) return <div className="evaluation-page-loading"><Skeleton active /></div>
  return <section className="evaluation-page">
    <header className="evaluation-header"><div><span className="section-eyebrow">AGENT EVALUATION</span><h1>评测中心</h1><p>使用固定套件验证 Agent 的理解、检索和安全边界；评测不会创建业务工单、审批或执行企业写操作。</p></div><Button type="primary" onClick={() => setCreateOpen(true)}>新建评测运行</Button></header>
    <div className="evaluation-safety-notice"><strong>安全边界</strong><span>离线合约校验不调用外部模型；在线只读评测需要显式确认，并由后端强制限制调用、Token、时长和费用。</span></div>
    <Tabs items={[
      { key: 'runs', label: '评测运行', children: selectedRun ? <div className="evaluation-detail"><Button type="link" onClick={() => navigate('/evaluations')}>← 返回运行列表</Button><div className="evaluation-detail-header"><div><span className="section-eyebrow">EVALUATION RUN</span><h2>{formatEvaluationSuiteName(selectedRun.suite_key, selectedRun.suite_name)}</h2><p>套件版本 {selectedRun.suite_version} · 运行编号 {selectedRun.run_id}</p></div><RunTag run={selectedRun} /></div><div className="evaluation-overview"><div><span>执行模式</span><strong>{evaluationRunModeView[selectedRun.mode].label}</strong></div><div><span>适用用例通过率</span><strong>{formatRate(selectedRun.pass_rate)}</strong></div><div><span>用例进度</span><strong>{selectedRun.completed_case_count} / {selectedRun.total_case_count}</strong></div><div><span>外部调用次数</span><strong>{selectedRun.call_count}</strong></div><div><span>失败 / 跳过</span><strong>{selectedRun.failed_case_count} / {selectedRun.skipped_case_count}</strong></div><div><span>安全错误</span><strong>{selectedRun.safe_error_code || '无'}</strong></div></div>{selectedRun.safe_error_summary && <div className="evaluation-error-notice">{selectedRun.safe_error_summary}</div>}<div className="evaluation-actions"><div><strong>基线与比较</strong><p>{baselineRunId === selectedRun.run_id ? '这次运行已是当前基线。' : '只有已完成的兼容运行可设为基线或用于比较。'}</p></div>{selectedRun.status === 'COMPLETED' && baselineRunId !== selectedRun.run_id && <Button onClick={setBaseline}>设为当前基线</Button>}</div>{comparison && <section className="evaluation-section"><h3>与当前基线的比较</h3><div className="evaluation-comparison-grid"><div><span>回归</span><strong>{comparison.regression_count}</strong></div><div><span>改善</span><strong>{comparison.improvement_count}</strong></div><div><span>持续失败</span><strong>{comparison.persistent_failure_count}</strong></div><div><span>异常或跳过</span><strong>{comparison.error_or_skipped_count}</strong></div></div></section>}<section className="evaluation-section"><h3>用例结果</h3>{cases.length === 0 ? <Empty description="评测尚未产出用例结果" /> : <Table rowKey="result_id" pagination={false} dataSource={cases} columns={[{ title: '用例', render: (_: unknown, item: EvaluationCaseResult) => formatEvaluationCaseName(item.case_id) }, { title: '类别', render: (_: unknown, item: EvaluationCaseResult) => evaluationCategoryView[item.category] }, { title: '结果', render: (_: unknown, item: EvaluationCaseResult) => <Tag color={evaluationCaseStatusView[item.status].color}>{evaluationCaseStatusView[item.status].label}</Tag> }, { title: '安全摘要', render: (_: unknown, item: EvaluationCaseResult) => item.safe_failure_summary || item.result_summary || '—' }, { title: '耗时', render: (_: unknown, item: EvaluationCaseResult) => item.duration_ms === null ? '—' : `${item.duration_ms} ms` }, { title: '', render: (_: unknown, item: EvaluationCaseResult) => (item.status === 'FAILED' || item.status === 'ERROR') && <Button type="link" onClick={() => { setArchiveTarget(item); setArchiveSummary(item.safe_failure_summary || '') }}>归档 Bad Case</Button> }]} />}</section></div> : <section className="evaluation-section"><h2>最近评测运行</h2><p>适用通过率由后端按“通过 ÷（总用例－跳过）”权威计算；仅在等待或执行中时每 2 秒刷新。</p><Table rowKey="run_id" pagination={false} dataSource={runs} columns={runColumns} locale={{ emptyText: <Empty description="尚未创建评测运行" /> }} /></section> },
      { key: 'bad-cases', label: `Bad Case（${badCases.length}）`, children: <section className="evaluation-section"><h2>Bad Case 治理</h2><p>只记录脱敏问题摘要和处理证据。必须先复测并验证，才能关闭。</p><div className="evaluation-bad-case-list">{badCases.length === 0 ? <Empty description="暂无已归档的 Bad Case" /> : badCases.map((item) => <BadCaseCard key={item.bad_case_id} item={item} reload={reloadBadCases} />)}</div></section> },
    ]} />
    <Modal title="新建评测运行" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => void createRun()} okText="创建运行" cancelText="取消" confirmLoading={submitting} okButtonProps={{ disabled: !suiteKey || (mode === 'LIVE_READ_ONLY' && !confirmLive) }}><div className="evaluation-create-form"><label><span>固定评测套件</span><Select value={suiteKey} onChange={setSuiteKey} options={suites.map((item) => ({ value: item.suite_key, label: `${formatEvaluationSuiteName(item.suite_key, item.suite_name)}（${item.case_count} 个用例）` }))} /></label>{suite && <p>版本 {suite.suite_version} · {suite.categories.map((item) => evaluationCategoryView[item]).join('、')}</p>}<label><span>执行模式</span><Radio.Group value={mode} onChange={(event) => { setMode(event.target.value); setConfirmLive(false) }}><Radio value="CONTRACT_ONLY">离线合约校验（默认，不调用外部模型）</Radio><Radio value="LIVE_READ_ONLY" disabled={!suite?.supported_modes.includes('LIVE_READ_ONLY')}>在线只读评测（受后端安全限额保护）</Radio></Radio.Group></label>{mode === 'LIVE_READ_ONLY' && <div className="evaluation-live-confirm"><p>在线只读评测最多 20 个用例，可能调用模型或嵌入服务；不会进入场景流程、审批、Action Gateway 或企业写工具。</p><Checkbox checked={confirmLive} onChange={(event) => setConfirmLive(event.target.checked)}>我确认允许受限的外部只读调用</Checkbox></div>}</div></Modal>
    <Modal title="归档 Bad Case" open={archiveTarget !== null} onCancel={() => setArchiveTarget(null)} onOk={() => void archive()} okText="确认归档" cancelText="取消" okButtonProps={{ disabled: !archiveSummary.trim() }}><div className="evaluation-create-form"><p>仅保存脱敏问题摘要，不要输入原始提示词、凭证或供应商原始响应。</p><label><span>问题摘要</span><Input.TextArea value={archiveSummary} onChange={(event) => setArchiveSummary(event.target.value)} rows={4} maxLength={500} /></label></div></Modal>
  </section>
}
