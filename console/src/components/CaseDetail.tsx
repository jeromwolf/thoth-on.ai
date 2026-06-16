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
      setSuccessMsg(`${assignee} 에게 배정되었습니다`)
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
      setSuccessMsg(`판정(${verdict}) 기록되었습니다`)
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
        <span className="empty-state-text">로딩 중...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="detail-panel">
        <div className="error-banner">{error}</div>
      </div>
    )
  }

  if (!data) return null

  const { explanation, signals, paths, history, verdicts: verdictList } = data

  return (
    <div className="detail-panel">
      {/* Header */}
      <div className="panel-header">
        <ScoreMeter score={data.score} size="lg" />
        <div className="panel-meta">
          <div className="panel-meta-row">
            <span className="panel-meta-key">케이스 ID</span>
            <span className="panel-meta-val mono">{data.case_id}</span>
          </div>
          <div className="panel-meta-row">
            <span className="panel-meta-key">고객 ID</span>
            <span className="panel-meta-val mono">{data.customer_id}</span>
          </div>
          <div className="panel-meta-row">
            <span className="panel-meta-key">상태</span>
            <span
              className="panel-meta-val"
              style={{ color: statusColor(data.status) }}
            >
              {statusLabel(data.status)}
            </span>
          </div>
          {data.ring_id && (
            <div className="panel-meta-row">
              <span className="panel-meta-key">링 ID</span>
              <span className="panel-meta-val mono" style={{ color: '#9b6dff' }}>
                {data.ring_id}
              </span>
            </div>
          )}
          {data.assignee && (
            <div className="panel-meta-row">
              <span className="panel-meta-key">담당자</span>
              <span className="panel-meta-val mono">{data.assignee}</span>
            </div>
          )}
          <div className="panel-meta-row">
            <span className="panel-meta-key">생성일</span>
            <span className="panel-meta-val mono text-muted">{formatTs(data.created_at)}</span>
          </div>
        </div>
        <div className="panel-actions">
          <button
            className="btn btn-primary btn-sm"
            onClick={() => onOpenGraph(data.customer_id)}
          >
            관계망 보기
          </button>
        </div>
      </div>

      {successMsg && (
        <div
          className="error-banner"
          style={{ background: 'rgba(46,184,114,0.1)', borderColor: 'rgba(46,184,114,0.3)', color: 'var(--c-safe)', marginBottom: 16 }}
        >
          {successMsg}
        </div>
      )}

      {/* Assign */}
      <div className="section">
        <div className="section-title">담당자 배정</div>
        <div className="assign-form">
          <input
            className="assign-input"
            placeholder="담당자 ID 입력"
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void handleAssign() }}
          />
          <button
            className="btn btn-primary btn-sm"
            disabled={assigning || !assignee.trim()}
            onClick={() => void handleAssign()}
          >
            {assigning ? <span className="loading-spinner" style={{ width: 12, height: 12 }} /> : '배정'}
          </button>
        </div>
      </div>

      {/* Verdict */}
      <div className="section">
        <div className="section-title">조사 판정</div>
        <textarea
          className="verdict-comment"
          placeholder="판정 코멘트 (선택)"
          value={verdictComment}
          onChange={(e) => setVerdictComment(e.target.value)}
        />
        <div className="verdict-row mt-2">
          <button
            className="btn btn-danger btn-sm"
            disabled={submitting}
            onClick={() => void handleVerdict('FRAUD')}
          >
            사기 확정
          </button>
          <button
            className="btn btn-success btn-sm"
            disabled={submitting}
            onClick={() => void handleVerdict('NORMAL')}
          >
            정상 처리
          </button>
          <button
            className="btn btn-warn btn-sm"
            disabled={submitting}
            onClick={() => void handleVerdict('HOLD')}
          >
            보류
          </button>
          {submitting && <span className="loading-spinner" style={{ width: 12, height: 12 }} />}
        </div>
      </div>

      {/* Signals */}
      {signals.length > 0 && (
        <div className="section">
          <div className="section-title">기여 신호 ({signals.length})</div>
          <div className="signals-grid">
            {signals.map((sig, i) => (
              <div className="signal-card" key={i}>
                <div className="signal-type">{sig.type}</div>
                {sig.weight !== null && (
                  <div>
                    <span className="signal-weight">{sig.weight.toFixed(2)}</span>
                    <span className="signal-weight-label"> 가중치</span>
                  </div>
                )}
                {Object.keys(sig.detail).length > 0 && (
                  <div className="signal-detail">
                    {Object.entries(sig.detail)
                      .slice(0, 3)
                      .map(([k, v]) => (
                        <div key={k}>
                          <span style={{ color: 'var(--text-muted)' }}>{k}: </span>
                          <span>{String(v)}</span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Explanation */}
      <div className="section">
        <div className="section-title">소명 분석</div>
        <div className="explanation-block">
          <div className="explanation-header">
            <span
              className={`badge ${explanation.accepted ? 'badge-grounded' : 'badge-hallucinated'}`}
            >
              {explanation.accepted ? '환각가드 통과' : '환각 감지'}
            </span>
            <span className="text-muted mono" style={{ fontSize: 10 }}>
              provider: {explanation.provider}
            </span>
          </div>
          <div className="explanation-text">{explanation.text}</div>
          <div className="grounding-detail">
            <div className="grounding-stat">
              <span className="grounding-stat-val">{explanation.grounding.num_known}</span>
              <span>실재 엔티티</span>
            </div>
            <div className="grounding-stat">
              <span className="grounding-stat-val">{explanation.grounding.cited_entities.length}</span>
              <span>인용 엔티티</span>
            </div>
            <div className="grounding-stat">
              <span
                className="grounding-stat-val"
                style={{ color: explanation.grounding.hallucinated.length > 0 ? 'var(--c-danger)' : 'var(--c-safe)' }}
              >
                {explanation.grounding.hallucinated.length}
              </span>
              <span>환각 항목</span>
            </div>
          </div>
          {explanation.grounding.hallucinated.length > 0 && (
            <div className="mt-2" style={{ fontSize: 10 }}>
              <span className="text-muted">환각: </span>
              {explanation.grounding.hallucinated.map((h) => (
                <span
                  key={h}
                  className="badge badge-hallucinated"
                  style={{ marginRight: 4 }}
                >
                  {h}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Evidence Paths */}
      {paths.length > 0 && (
        <div className="section">
          <div className="section-title">근거 경로 ({paths.length})</div>
          {paths.map((path, i) => (
            <div className="path-card" key={i}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span className="badge badge-signal">{path.signal_type}</span>
                {path.weight !== null && (
                  <span className="mono text-muted" style={{ fontSize: 10 }}>
                    w={path.weight.toFixed(2)}
                  </span>
                )}
              </div>
              <div className="path-label">{path.label}</div>
              {path.nodes.length > 0 && (
                <div className="path-chain">
                  {path.nodes.map((node, ni) => {
                    const edge = path.edges[ni]
                    return (
                      <span key={node.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
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

      {/* Verdict History */}
      {verdictList.length > 0 && (
        <div className="section">
          <div className="section-title">판정 이력</div>
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
                  <td style={{ color: v.label === 'FRAUD' ? 'var(--c-danger)' : v.label === 'NORMAL' ? 'var(--c-safe)' : 'var(--c-warning)' }}>
                    {v.label}
                  </td>
                  <td>{v.actor}</td>
                  <td className="text-muted">{v.comment || '—'}</td>
                  <td className="text-muted">{formatTs(v.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Status History */}
      {history.length > 0 && (
        <div className="section">
          <div className="section-title">상태 변경 이력</div>
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
                  <td style={{ color: statusColor(h.to_status) }}>{statusLabel(h.to_status)}</td>
                  <td>{h.actor}</td>
                  <td className="text-muted">{formatTs(h.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
