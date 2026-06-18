import { useState, useEffect } from 'react'
import { retrain, getActiveModel } from '../api/endpoints'
import type { RetrainResponse, RetrainMetrics, ActiveModel } from '../types'

// ─── helpers ────────────────────────────────────────────────────────────────

function pct(n: number) {
  return (n * 100).toFixed(1) + '%'
}

function delta(n: number, precision = 3) {
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(precision)}`
}

function deltaClass(n: number): string {
  if (n > 0.001) return 'retrain-delta-pos'
  if (n < -0.001) return 'retrain-delta-neg'
  return 'retrain-delta-neutral'
}

function formatTrainedAt(raw: string): string {
  try {
    const d = new Date(raw)
    return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' })
  } catch {
    return raw
  }
}

// ─── ActiveModelBadge ────────────────────────────────────────────────────────

interface ActiveModelBadgeProps {
  model: ActiveModel | null
  loading: boolean
}

function ActiveModelBadge({ model, loading }: ActiveModelBadgeProps) {
  if (loading) {
    return (
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '5px 10px',
          borderRadius: 6,
          background: 'var(--surface-2, #f4f5f7)',
          fontSize: 12,
          color: 'var(--ink-4)',
        }}
      >
        <span className="loading-spinner" style={{ width: 10, height: 10, borderWidth: 2 }} />
        모델 상태 확인 중…
      </div>
    )
  }

  if (!model) return null

  const isActive = model.active

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        padding: '5px 10px',
        borderRadius: 6,
        background: isActive ? 'var(--c-safe-bg, #edfaf1)' : 'var(--surface-2, #f4f5f7)',
        border: `1px solid ${isActive ? 'var(--c-safe-border, #b7eacb)' : 'var(--border, #e2e4e9)'}`,
        fontSize: 12,
        color: isActive ? 'var(--c-safe-text, #1a7a42)' : 'var(--ink-4)',
        fontVariantNumeric: 'tabular-nums',
        flexWrap: 'wrap' as const,
      }}
    >
      {/* status dot */}
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: isActive ? 'var(--c-safe, #22c55e)' : 'var(--ink-5, #c0c4ce)',
          flexShrink: 0,
        }}
      />
      {isActive ? (
        <span>
          <strong style={{ fontWeight: 600 }}>활성 모델:</strong>{' '}
          {model.model_kind?.toUpperCase() ?? '–'}
          {model.trained_at && (
            <> · 학습 {formatTrainedAt(model.trained_at)}</>
          )}
          {model.n_samples !== undefined && (
            <> · 표본 {model.n_samples.toLocaleString()}
              {model.n_positive !== undefined && (
                <>(양성 {model.n_positive.toLocaleString()})</>
              )}
            </>
          )}
          {model.feature_count !== undefined && (
            <> · 피처 {model.feature_count}개</>
          )}
        </span>
      ) : (
        <span>활성 모델 없음 — 룰+그래프만 사용</span>
      )}
    </div>
  )
}

// ─── sub-components ─────────────────────────────────────────────────────────

interface MetricsRowProps {
  label: string
  baseline: RetrainMetrics
  feedback: RetrainMetrics
}

function MetricsRow({ label, baseline, feedback }: MetricsRowProps) {
  type MetricKey = keyof RetrainMetrics
  const rows: { key: MetricKey; display: string; fmt: (v: number) => string }[] = [
    { key: 'recall', display: '재현율(Recall)', fmt: pct },
    { key: 'precision', display: '정밀도(Precision)', fmt: pct },
    { key: 'f1', display: 'F1', fmt: (v) => v.toFixed(3) },
    { key: 'auc', display: 'AUC', fmt: (v) => v.toFixed(3) },
    { key: 'fpr', display: 'FPR', fmt: pct },
    { key: 'tp', display: 'TP', fmt: (v) => String(v) },
    { key: 'fp', display: 'FP', fmt: (v) => String(v) },
    { key: 'fn', display: 'FN', fmt: (v) => String(v) },
    { key: 'tn', display: 'TN', fmt: (v) => String(v) },
  ]

  return (
    <div className="retrain-metric-section">
      <div className="kpi-group-label" style={{ marginBottom: 8 }}>{label}</div>
      <table className="retrain-table">
        <thead>
          <tr>
            <th>지표</th>
            <th>Baseline</th>
            <th>Feedback</th>
            <th>변화</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, display, fmt }) => {
            const diff = feedback[key] - baseline[key]
            return (
              <tr key={key}>
                <td className="retrain-metric-name">{display}</td>
                <td className="retrain-metric-val">{fmt(baseline[key])}</td>
                <td className="retrain-metric-val">{fmt(feedback[key])}</td>
                <td className={`retrain-metric-delta ${deltaClass(diff)}`}>
                  {delta(diff, key === 'tp' || key === 'fp' || key === 'fn' || key === 'tn' ? 0 : 3)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ─── main component ──────────────────────────────────────────────────────────

export function RetrainPanel() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RetrainResponse | null>(null)
  const [emptyMsg, setEmptyMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [persist, setPersist] = useState(false)
  const [activeModel, setActiveModel] = useState<ActiveModel | null>(null)
  const [activeModelLoading, setActiveModelLoading] = useState(true)

  // Fetch active model status on mount
  useEffect(() => {
    setActiveModelLoading(true)
    getActiveModel()
      .then(setActiveModel)
      .catch(() => setActiveModel({ active: false }))
      .finally(() => setActiveModelLoading(false))
  }, [])

  async function handleRetrain() {
    setLoading(true)
    setResult(null)
    setEmptyMsg(null)
    setErrorMsg(null)
    try {
      const res = await retrain('rf', 5, persist)
      setResult(res)
      // Refresh active model status after successful retrain
      if (persist) {
        getActiveModel()
          .then(setActiveModel)
          .catch(() => {/* silent */})
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '알 수 없는 오류'
      // 409 — insufficient feedback labels
      if (msg.includes('최소 양성') || msg.includes('판정 라벨') || msg.includes('부족')) {
        setEmptyMsg('아직 누적된 조사관 판정이 부족합니다. 케이스 큐에서 판정을 입력한 뒤 다시 시도하세요.')
      } else {
        setErrorMsg(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="kpi-panel">
      <div className="kpi-inner">

        {/* Page head */}
        <div className="kpi-page-head">
          <span className="kpi-page-title">판정 피드백 재학습</span>
          <span className="kpi-page-sub">
            조사관 판정(FRAUD / NORMAL)을 운영 라벨로 반영해 RF 모델을 재학습
          </span>
        </div>

        {/* Active model badge */}
        <div style={{ marginBottom: 20 }}>
          <ActiveModelBadge model={activeModel} loading={activeModelLoading} />
        </div>

        {/* Description card */}
        <div className="chart-card" style={{ marginBottom: 20 }}>
          <p style={{ margin: 0, color: 'var(--ink-3)', fontSize: 13, lineHeight: 1.65 }}>
            케이스 큐에서 조사관이 입력한 판정(FRAUD / NORMAL)을 그라운드 트루스로 활용해
            Random Forest 모델을 교차검증으로 재평가합니다. <br />
            <strong style={{ color: 'var(--ink)' }}>Baseline</strong>은 원본 GT 라벨,{' '}
            <strong style={{ color: 'var(--ink)' }}>Feedback</strong>은 조사관 판정이
            반영된 라벨 기준으로 별도 평가됩니다.
          </p>
        </div>

        {/* Run button + persist checkbox */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24, flexWrap: 'wrap' }}>
          <button
            className="btn btn-primary"
            onClick={handleRetrain}
            disabled={loading}
            style={{ minWidth: 140 }}
          >
            {loading ? (
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="loading-spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                재학습 중...
              </span>
            ) : (
              '재학습 실행'
            )}
          </button>

          {/* Persist checkbox */}
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.5 : 1,
              fontSize: 13,
              color: 'var(--ink-2)',
              userSelect: 'none',
            }}
          >
            <input
              type="checkbox"
              checked={persist}
              onChange={(e) => setPersist(e.target.checked)}
              disabled={loading}
              style={{ width: 14, height: 14, accentColor: 'var(--accent)', cursor: 'inherit' }}
            />
            라이브 스코어링에 활성화 (모델 영속화)
          </label>

          {loading && (
            <span style={{ fontSize: 12, color: 'var(--ink-4)' }}>
              교차검증을 실행 중입니다. 잠시 기다려 주세요.
            </span>
          )}
        </div>

        {/* Persist activated notice */}
        {result?.persisted && (
          <div
            className="chart-card"
            style={{ borderLeft: '3px solid var(--c-safe)', marginBottom: 20 }}
          >
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="var(--c-safe)" strokeWidth="1.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 1 }}>
                <circle cx="8" cy="8" r="6.5" />
                <polyline points="5,8.5 7,10.5 11,6" />
              </svg>
              <span style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.6 }}>
                모델이 활성화되어 다음 스코어링부터 반영됩니다.
                {result.model_path && (
                  <> 저장 경로: <code style={{ fontSize: 11, background: 'var(--surface-2, #f4f5f7)', padding: '1px 4px', borderRadius: 3 }}>{result.model_path}</code></>
                )}
              </span>
            </div>
          </div>
        )}

        {/* 409 — insufficient labels */}
        {emptyMsg && (
          <div className="chart-card" style={{ borderLeft: '3px solid var(--c-warning)', marginBottom: 20 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="var(--c-warning)" strokeWidth="1.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 1 }}>
                <circle cx="8" cy="8" r="6.5" />
                <line x1="8" y1="5" x2="8" y2="8.5" />
                <circle cx="8" cy="11" r="0.8" fill="var(--c-warning)" stroke="none" />
              </svg>
              <span style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.6 }}>{emptyMsg}</span>
            </div>
          </div>
        )}

        {/* 503 / other errors */}
        {errorMsg && (
          <div className="banner banner-error" style={{ marginBottom: 20 }}>
            백엔드 미가용 또는 오류: {errorMsg}
          </div>
        )}

        {/* Results */}
        {result && (
          <>
            {/* Delta headline */}
            <div className="kpi-grid" style={{ marginBottom: 24 }}>
              <div
                className="kpi-card"
                style={{ '--kpi-accent': result.delta_f1 >= 0 ? 'var(--c-safe)' : 'var(--c-danger)' } as React.CSSProperties}
              >
                <div className="kpi-label">ΔF1</div>
                <div className="kpi-value" style={{ color: result.delta_f1 >= 0 ? 'var(--c-safe)' : 'var(--c-danger)' }}>
                  {delta(result.delta_f1, 3)}
                </div>
                <div className="kpi-sub">Feedback − Baseline</div>
              </div>
              <div
                className="kpi-card"
                style={{ '--kpi-accent': result.delta_auc >= 0 ? 'var(--c-safe)' : 'var(--c-danger)' } as React.CSSProperties}
              >
                <div className="kpi-label">ΔAUC</div>
                <div className="kpi-value" style={{ color: result.delta_auc >= 0 ? 'var(--c-safe)' : 'var(--c-danger)' }}>
                  {delta(result.delta_auc, 3)}
                </div>
                <div className="kpi-sub">Feedback − Baseline</div>
              </div>
              <div className="kpi-card" style={{ '--kpi-accent': 'var(--accent)' } as React.CSSProperties}>
                <div className="kpi-label">모델 / 폴드</div>
                <div className="kpi-value" style={{ fontSize: 18 }}>{result.model_kind.toUpperCase()} / {result.n_folds}-fold</div>
                <div className="kpi-sub">교차검증 방식</div>
              </div>
            </div>

            {/* Provenance */}
            <div className="kpi-group-label">데이터 출처(Provenance)</div>
            <div className="kpi-grid" style={{ marginBottom: 24 }}>
              {[
                { label: '전체 샘플', value: result.provenance.n_total, accent: 'var(--accent)' },
                { label: '판정 반영', value: result.provenance.n_feedback, accent: 'var(--c-safe)' },
                { label: 'Override', value: result.provenance.n_overrides, accent: 'var(--c-warning)', sub: '조사관이 변경' },
                { label: '재확인(Agree)', value: result.provenance.n_agree, accent: 'var(--c-purple)', sub: '조사관이 일치' },
                { label: 'GT 유지', value: result.provenance.n_base, accent: 'var(--ink-4)', sub: '판정 없는 샘플' },
              ].map((c) => (
                <div
                  key={c.label}
                  className="kpi-card"
                  style={{ '--kpi-accent': c.accent } as React.CSSProperties}
                >
                  <div className="kpi-label">{c.label}</div>
                  <div className="kpi-value">{c.value}</div>
                  {c.sub && <div className="kpi-sub">{c.sub}</div>}
                </div>
              ))}
            </div>

            {/* Metrics comparison table */}
            <MetricsRow
              label="성능 비교 (Baseline vs Feedback)"
              baseline={result.baseline}
              feedback={result.feedback}
            />

            {/* Honesty disclaimer */}
            <div className="disclaimer" style={{ marginTop: 20 }}>
              <strong>정직성 주의</strong> — {result.note}
              {' '}
              <span style={{ opacity: 0.75 }}>
                Baseline과 Feedback은 서로 다른 라벨 집합으로 평가됩니다. Delta 수치는 참고치이며 직접적인 성능 향상의 증거가 아닙니다.
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
