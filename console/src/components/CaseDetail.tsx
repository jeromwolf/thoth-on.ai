import { useState, useEffect } from 'react'
import { fetchCaseDetail, assignCase, recordVerdict } from '../api/endpoints'
import type { CaseDetailResponse, VerdictEntry } from '../types'
import { ScoreMeter } from './ScoreMeter'
import { statusColor, statusLabel, formatTs } from '../utils/score'
import type { VerdictRequest } from '../api/endpoints'

interface Props {
  caseId: string
  onOpenGraph: (customerId: string) => void
}

export function CaseDetail({ caseId, onOpenGraph }: Props) {
  const [data, setData] = useState<CaseDetailResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [assignee, setAssignee] = useState('')
  const [assigning, setAssigning] = useState(false)
  const [verdictComment, setVerdictComment] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!caseId) return
    setLoading(true)
    setError(null)
    setData(null)
    setSuccessMsg(null)
    fetchCaseDetail(caseId)
      .then((d) => { setData(d); setAssignee(d.assignee || '') })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '로드 실패'))
      .finally(() => setLoading(false))
  }, [caseId])

  async function handleAssign() {
    if (!assignee.trim()) return
    setAssigning(true)
    try {
      await assignCase(caseId, { assignee: assignee.trim() })
      setSuccessMsg(`${assignee} 담당자에게 배정되었습니다`)
      const fresh = await fetchCaseDetail(caseId)
      setData(fresh)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '배정 실패')
    } finally {
      setAssigning(false)
    }
  }

  async function handleVerdict(verdict: VerdictRequest['verdict']) {
    setSubmitting(true)
    try {
      await recordVerdict(caseId, { verdict, comment: verdictComment })
      const labels: Record<string, string> = { FRAUD: '사기 확정', NORMAL: '정상 처리', HOLD: '보류' }
      setSuccessMsg(`판정이 기록되었습니다 — ${labels[verdict] ?? verdict}`)
      setVerdictComment('')
      const fresh = await fetchCaseDetail(caseId)
      setData(fresh)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '판정 실패')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="empty-state">
        <div className="loading-spinner" />
        <span className="empty-text">사건 정보를 불러오는 중...</span>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="detail-panel">
        <div className="detail-inner">
          <div className="banner banner-error">{error}</div>
        </div>
      </div>
    )
  }

  if (!data) return null

  const { explanation, signals, paths, history, verdicts: verdictList } = data
  const grounded = explanation.accepted
  const hallucinatedCount = explanation.grounding.hallucinated.length

  return (
    <div className="detail-panel">
      <div className="detail-inner">
        {/* Summary card */}
        <div className="summary-card">
          <ScoreMeter score={data.score} size="lg" />

          <div className="summary-meta">
            <div>
              <div className="summary-title">사건 번호</div>
              <div className="summary-case-id">{data.case_id}</div>
            </div>
            <div className="meta-grid">
              <div className="meta-cell">
                <span className="meta-cell-key">고객 ID</span>
                <span className="meta-cell-val">{data.customer_id}</span>
              </div>
              <div className="meta-cell">
                <span className="meta-cell-key">상태</span>
                <span className="meta-cell-val" style={{ color: statusColor(data.status), fontWeight: 600 }}>
                  {statusLabel(data.status)}
                </span>
              </div>
              {data.ring_id && (
                <div className="meta-cell">
                  <span className="meta-cell-key">의심 조직</span>
                  <span className="meta-cell-val" style={{ color: 'var(--c-purple)' }}>
                    {data.ring_id}
                  </span>
                </div>
              )}
              <div className="meta-cell">
                <span className="meta-cell-key">담당자</span>
                <span className="meta-cell-val">{data.assignee || '미배정'}</span>
              </div>
              <div className="meta-cell">
                <span className="meta-cell-key">접수일</span>
                <span className="meta-cell-val">{formatTs(data.created_at)}</span>
              </div>
            </div>
          </div>

          <div className="summary-actions">
            <button
              className="btn btn-primary btn-block"
              onClick={() => onOpenGraph(data.customer_id)}
            >
              관계망 보기
            </button>
            <span className="text-muted" style={{ fontSize: 11, lineHeight: 1.4 }}>
              고객을 둘러싼 계좌·병원·정비소 등의 연결을 시각화합니다.
            </span>
          </div>
        </div>

        {error && <div className="banner banner-error">{error}</div>}
        {successMsg && <div className="banner banner-success">{successMsg}</div>}

        {/* Explanation — featured */}
        <div className="section">
          <div className="section-head">
            <span className="section-title">왜 의심되는가</span>
            <span className="section-hint">AI 분석가가 작성한 소명문</span>
          </div>
          <div className="explanation-card">
            <div className="explanation-bar">
              <span className="explanation-bar-title">AI 소명 분석</span>
              <span className={`badge ${grounded ? 'badge-grounded' : 'badge-hallucinated'}`} style={{ marginLeft: 'auto' }}>
                {grounded ? '검증 통과' : '검증 주의'}
              </span>
              <span className="text-muted mono" style={{ fontSize: 11 }}>
                {explanation.provider}
              </span>
            </div>
            <div className="explanation-body">
              <blockquote className="explanation-quote">{explanation.text}</blockquote>

              <div className={`guard-note${grounded ? '' : ' warn'}`}>
                <span aria-hidden style={{ flexShrink: 0, fontWeight: 700 }}>
                  {grounded ? '✓' : '!'}
                </span>
                <span>
                  {grounded
                    ? '환각 가드 통과 — AI가 실제로 존재하는 근거만 인용했는지 검증되었습니다.'
                    : '환각 가드 주의 — 실제 데이터에 없는 항목이 인용되었을 수 있어 검토가 필요합니다.'}
                </span>
              </div>

              <div className="grounding-strip">
                <div className="grounding-stat">
                  <div className="grounding-stat-val">{explanation.grounding.num_known}</div>
                  <div className="grounding-stat-label">실재하는 엔티티</div>
                </div>
                <div className="grounding-stat">
                  <div className="grounding-stat-val">{explanation.grounding.cited_entities.length}</div>
                  <div className="grounding-stat-label">소명문이 인용한 엔티티</div>
                </div>
                <div className="grounding-stat">
                  <div
                    className="grounding-stat-val"
                    style={{ color: hallucinatedCount > 0 ? 'var(--c-danger)' : 'var(--c-safe)' }}
                  >
                    {hallucinatedCount}
                  </div>
                  <div className="grounding-stat-label">환각(허위) 항목</div>
                </div>
              </div>

              {hallucinatedCount > 0 && (
                <div className="hallucinated-list">
                  <span className="text-muted" style={{ fontSize: 12 }}>환각 의심 항목:</span>
                  {explanation.grounding.hallucinated.map((h) => (
                    <span key={h} className="badge badge-hallucinated">{h}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Signals */}
        {signals.length > 0 && (
          <div className="section">
            <div className="section-head">
              <span className="section-title">기여 신호</span>
              <span className="section-count">{signals.length}</span>
              <span className="section-hint">위험 점수를 높인 근거 항목</span>
            </div>
            <div className="signals-grid">
              {signals.map((sig, i) => (
                <div className="signal-card" key={i}>
                  <div className="signal-top">
                    <span className="signal-type">{sig.type}</span>
                    {sig.weight !== null && (
                      <span className="signal-weight-wrap">
                        <span className="signal-weight">{sig.weight.toFixed(2)}</span>
                        <span className="signal-weight-label">가중치</span>
                      </span>
                    )}
                  </div>
                  {Object.keys(sig.detail).length > 0 && (
                    <div className="signal-detail">
                      {Object.entries(sig.detail).slice(0, 3).map(([k, v]) => (
                        <div key={k}>
                          <span className="k">{k}: </span>
                          <span className="v">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Evidence Paths */}
        {paths.length > 0 && (
          <div className="section">
            <div className="section-head">
              <span className="section-title">근거 경로</span>
              <span className="section-count">{paths.length}</span>
              <span className="section-hint">관계망에서 추출한 의심 연결 고리</span>
            </div>
            {paths.map((path, i) => (
              <div className="path-card" key={i}>
                <div className="path-head">
                  <span className="badge badge-signal">{path.signal_type}</span>
                  {path.weight !== null && (
                    <span className="mono text-muted" style={{ fontSize: 11 }}>
                      가중치 {path.weight.toFixed(2)}
                    </span>
                  )}
                </div>
                <div className="path-label">{path.label}</div>
                {path.nodes.length > 0 && (
                  <div className="path-chain">
                    {path.nodes.map((node, ni) => {
                      const edge = path.edges[ni]
                      return (
                        <span key={node.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                          <span className="path-node" title={`${node.type}: ${node.id}`}>
                            {node.label}
                          </span>
                          {edge && (
                            <>
                              <span className="path-arrow">—</span>
                              <span className="path-edge-label">{edge.type}</span>
                              <span className="path-arrow">→</span>
                            </>
                          )}
                        </span>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Actions: Assign + Verdict */}
        <div className="section">
          <div className="section-head">
            <span className="section-title">조사 처리</span>
            <span className="section-hint">담당자 배정 후 최종 판정을 기록하세요</span>
          </div>

          <div className="form-card" style={{ marginBottom: 12 }}>
            <span className="field-label" style={{ display: 'block', marginBottom: 10 }}>
              담당자 배정
            </span>
            <div className="assign-form">
              <input
                className="input"
                placeholder="담당자 ID를 입력하세요"
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleAssign() }}
                aria-label="담당자 ID"
              />
              <button
                className="btn btn-primary"
                disabled={assigning || !assignee.trim()}
                onClick={() => void handleAssign()}
              >
                {assigning ? <span className="loading-spinner" style={{ width: 14, height: 14 }} /> : '배정'}
              </button>
            </div>
          </div>

          <div className="form-card">
            <span className="field-label" style={{ display: 'block', marginBottom: 10 }}>
              최종 판정
            </span>
            <textarea
              className="textarea"
              placeholder="판정 사유나 메모를 남겨주세요 (선택)"
              value={verdictComment}
              onChange={(e) => setVerdictComment(e.target.value)}
              aria-label="판정 코멘트"
            />
            <div className="verdict-row" style={{ marginTop: 12 }}>
              <button
                className="btn btn-danger"
                disabled={submitting}
                onClick={() => void handleVerdict('FRAUD')}
              >
                사기 확정
              </button>
              <button
                className="btn btn-success"
                disabled={submitting}
                onClick={() => void handleVerdict('NORMAL')}
              >
                정상 처리
              </button>
              <button
                className="btn btn-warn"
                disabled={submitting}
                onClick={() => void handleVerdict('HOLD')}
              >
                보류
              </button>
              {submitting && <span className="loading-spinner" style={{ width: 16, height: 16 }} />}
            </div>
          </div>
        </div>

        {/* Verdict History */}
        {verdictList.length > 0 && (
          <div className="section">
            <div className="section-head">
              <span className="section-title">판정 이력</span>
              <span className="section-count">{verdictList.length}</span>
            </div>
            <div className="table-wrap">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>판정</th>
                    <th>담당자</th>
                    <th>코멘트</th>
                    <th>일시</th>
                  </tr>
                </thead>
                <tbody>
                  {verdictList.map((v: VerdictEntry, i: number) => (
                    <tr key={i}>
                      <td style={{ color: v.label === 'FRAUD' ? 'var(--c-danger)' : v.label === 'NORMAL' ? 'var(--c-safe)' : 'var(--c-warning)', fontWeight: 600 }}>
                        {v.label === 'FRAUD' ? '사기' : v.label === 'NORMAL' ? '정상' : v.label === 'HOLD' ? '보류' : v.label}
                      </td>
                      <td>{v.actor}</td>
                      <td className="text-muted">{v.comment || '—'}</td>
                      <td className="mono text-muted">{formatTs(v.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Status History */}
        {history.length > 0 && (
          <div className="section">
            <div className="section-head">
              <span className="section-title">상태 변경 이력</span>
              <span className="section-count">{history.length}</span>
            </div>
            <div className="table-wrap">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>이전</th>
                    <th>이후</th>
                    <th>처리자</th>
                    <th>일시</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i}>
                      <td className="text-muted">{statusLabel(h.from_status)}</td>
                      <td style={{ color: statusColor(h.to_status), fontWeight: 600 }}>{statusLabel(h.to_status)}</td>
                      <td>{h.actor}</td>
                      <td className="mono text-muted">{formatTs(h.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
