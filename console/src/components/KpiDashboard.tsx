import { useState, useEffect } from 'react'
import type { CSSProperties } from 'react'
import { fetchKpi } from '../api/endpoints'
import type { KpiResponse } from '../types'
import { statusLabel, statusColor } from '../utils/score'

interface KpiCard {
  label: string
  value: string | number
  accent: string
  sub?: string
  estimate?: boolean
}

export function KpiDashboard() {
  const [data, setData] = useState<KpiResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchKpi()
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'KPI 로드 실패'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="empty-state">
        <div className="loading-spinner" />
        <span className="empty-text">현황 데이터를 불러오는 중...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="kpi-panel">
        <div className="kpi-inner">
          <div className="banner banner-error">{error}</div>
        </div>
      </div>
    )
  }

  if (!data) return null

  const maxDist = Math.max(...Object.values(data.status_distribution), 1)
  const separationPct = Math.min((data.score_separation / 100) * 100, 100)
  const savingsOkKrw = (data.estimated_savings_krw / 100_000_000).toFixed(2)

  const primaryCards: KpiCard[] = [
    { label: '총 사건', value: data.total_cases, accent: 'var(--accent)' },
    {
      label: '고위험 사건',
      value: data.high_risk_cases,
      accent: 'var(--c-danger)',
      sub: `탐지율 ${data.detection_rate_pct}%`,
    },
    { label: '사기 판정', value: data.fraud_verdicts, accent: 'var(--c-danger)' },
    { label: '의심 조직(군집)', value: data.suspected_rings, accent: 'var(--c-purple)' },
  ]

  const scoreCards: KpiCard[] = [
    { label: '평균 위험 점수', value: data.avg_score.toFixed(1), accent: 'var(--c-warning)', sub: '전체 사건 기준' },
    { label: '고위험 평균', value: data.avg_high_risk_score.toFixed(1), accent: 'var(--c-danger)', sub: `임계치 ${data.threshold}점 이상` },
    { label: '저위험 평균', value: data.avg_low_risk_score.toFixed(1), accent: 'var(--c-safe)', sub: '임계치 미만' },
    { label: '점수 분리도', value: data.score_separation.toFixed(1), accent: 'var(--c-warning)', sub: '고위험 - 저위험 차이' },
  ]

  const estimateCards: KpiCard[] = [
    {
      label: '일일 처리량',
      value: `${data.daily_throughput_estimate}건`,
      accent: 'var(--accent)',
      sub: '조사관 1인 기준',
      estimate: true,
    },
    {
      label: '예상 절감액',
      value: `${savingsOkKrw}억 원`,
      accent: 'var(--c-safe)',
      sub: '사기판정 × 500만 원 가정',
      estimate: true,
    },
  ]

  const renderGrid = (cards: KpiCard[]) => (
    <div className="kpi-grid">
      {cards.map((card) => (
        <div className="kpi-card" key={card.label} style={{ '--kpi-accent': card.accent } as CSSProperties}>
          <div className="kpi-label">
            {card.label}
            {card.estimate && <span className="tag-estimate">추정</span>}
          </div>
          <div className="kpi-value">{card.value}</div>
          {card.sub && <div className="kpi-sub">{card.sub}</div>}
        </div>
      ))}
    </div>
  )

  return (
    <div className="kpi-panel">
      <div className="kpi-inner">
        <div className="kpi-page-head">
          <span className="kpi-page-title">현황판</span>
          <span className="kpi-page-sub">위험 임계치 {data.threshold}점 기준 · 경영 보고용 요약</span>
        </div>

        {/* Primary KPIs */}
        <div className="kpi-group-label">핵심 지표</div>
        {renderGrid(primaryCards)}

        {/* Score gauge */}
        <div className="gauge-card">
          <div className="gauge-head">
            <span className="gauge-title">점수 분리도</span>
            <span className="gauge-value">{data.score_separation.toFixed(1)}점</span>
          </div>
          <div className="gauge-track">
            <div className="gauge-fill" style={{ width: `${separationPct}%` }} />
          </div>
          <div className="kpi-sub" style={{ marginTop: 10 }}>
            고위험군과 저위험군의 평균 점수 차이가 클수록 모델이 사기를 잘 구분하고 있다는 의미입니다.
          </div>
        </div>

        {/* Score detail */}
        <div className="kpi-group-label">점수 분석</div>
        {renderGrid(scoreCards)}

        {/* Status distribution */}
        {Object.keys(data.status_distribution).length > 0 && (
          <div className="chart-card">
            <div className="chart-title">사건 상태 분포</div>
            {Object.entries(data.status_distribution)
              .sort(([, a], [, b]) => b - a)
              .map(([status, count]) => {
                const pct = (count / maxDist) * 100
                return (
                  <div className="dist-row" key={status}>
                    <span className="dist-label" style={{ color: statusColor(status) }}>
                      <span className="badge-dot" />
                      {statusLabel(status)}
                    </span>
                    <div className="dist-bar-wrap">
                      <div className="dist-bar" style={{ width: `${pct}%`, background: statusColor(status) }} />
                    </div>
                    <span className="dist-count">{count}</span>
                  </div>
                )
              })}
          </div>
        )}

        {/* Estimates */}
        <div className="kpi-group-label">추정 지표 (시범 데이터 기준)</div>
        {renderGrid(estimateCards)}

        <div className="disclaimer">
          <strong>추정 지표 가정</strong> — {data.savings_assumption} 추정 수치는 시범 데이터를 토대로
          계산된 참고용 값이며 실제 성과와 다를 수 있습니다.
        </div>
      </div>
    </div>
  )
}
