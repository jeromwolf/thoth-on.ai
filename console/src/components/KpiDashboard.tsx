import { useState, useEffect } from 'react'
import { fetchKpi } from '../api/endpoints'
import type { KpiResponse } from '../types'
import { statusLabel, statusColor } from '../utils/score'

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
        <span className="empty-state-text">KPI 로딩 중...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="kpi-panel">
        <div className="error-banner">{error}</div>
      </div>
    )
  }

  if (!data) return null

  const maxDist = Math.max(...Object.values(data.status_distribution), 1)
  const separationPct = Math.min((data.score_separation / 100) * 100, 100)

  // 절감액 포맷 (억 원 단위)
  const savingsOkKrw = (data.estimated_savings_krw / 100_000_000).toFixed(2)

  const kpiCards = [
    {
      label: '총 케이스',
      value: data.total_cases,
      accent: 'var(--accent)',
    },
    {
      label: '고위험 케이스',
      value: data.high_risk_cases,
      accent: 'var(--c-danger)',
      sub: `탐지율 ${data.detection_rate_pct}%`,
    },
    {
      label: '사기 판정',
      value: data.fraud_verdicts,
      accent: '#f03e3e',
    },
    {
      label: '의심 링(군집)',
      value: data.suspected_rings,
      accent: '#9b6dff',
    },
    {
      label: '평균 점수',
      value: data.avg_score.toFixed(1),
      accent: 'var(--c-warning)',
      sub: '전체 케이스',
    },
    {
      label: '고위험 평균',
      value: data.avg_high_risk_score.toFixed(1),
      accent: 'var(--c-danger)',
      sub: `임계치: ${data.threshold}`,
    },
    {
      label: '저위험 평균',
      value: data.avg_low_risk_score.toFixed(1),
      accent: 'var(--c-safe)',
      sub: `임계치 미만`,
    },
    {
      label: '점수 분리도',
      value: data.score_separation.toFixed(1),
      accent: 'var(--c-warning)',
      sub: '고위험 - 저위험 차이',
    },
    {
      label: '일일 처리량 추정',
      value: `${data.daily_throughput_estimate}건`,
      accent: 'var(--accent)',
      sub: '조사관 1인 기준 (추정)',
      estimate: true,
    },
    {
      label: '절감액 추정',
      value: `${savingsOkKrw}억 원`,
      accent: '#20c997',
      sub: '사기판정 × 500만 원 가정',
      estimate: true,
    },
  ]

  return (
    <div className="kpi-panel">
      <div
        className="section-title"
        style={{ marginBottom: 16, fontSize: 10, letterSpacing: '0.15em', color: 'var(--text-muted)' }}
      >
        KPI 대시보드
        <span
          className="mono"
          style={{ marginLeft: 8, fontSize: 9, color: 'var(--text-dim)' }}
        >
          임계치 {data.threshold}
        </span>
      </div>

      {/* KPI cards */}
      <div className="kpi-grid">
        {kpiCards.map((card) => (
          <div
            className="kpi-card"
            key={card.label}
            style={{ '--kpi-accent': card.accent } as React.CSSProperties}
          >
            <div className="kpi-label">
              {card.label}
              {'estimate' in card && card.estimate && (
                <span
                  style={{
                    marginLeft: 4,
                    fontSize: 8,
                    color: 'var(--text-dim)',
                    border: '1px solid var(--text-dim)',
                    borderRadius: 2,
                    padding: '0 2px',
                    verticalAlign: 'middle',
                  }}
                >
                  추정
                </span>
              )}
            </div>
            <div className="kpi-value">{card.value}</div>
            {card.sub && <div className="kpi-sub">{card.sub}</div>}
          </div>
        ))}
      </div>

      {/* Score separation gauge */}
      <div className="separation-gauge">
        <span className="gauge-label">점수 분리도</span>
        <div className="gauge-track">
          <div className="gauge-fill" style={{ width: `${separationPct}%` }} />
        </div>
        <span className="gauge-value">{data.score_separation.toFixed(1)}pt</span>
      </div>

      {/* Status distribution */}
      {Object.keys(data.status_distribution).length > 0 && (
        <div className="kpi-dist-chart" style={{ marginTop: 16 }}>
          <div className="section-title" style={{ marginBottom: 12 }}>상태 분포</div>
          {Object.entries(data.status_distribution)
            .sort(([, a], [, b]) => b - a)
            .map(([status, count]) => {
              const pct = (count / maxDist) * 100
              return (
                <div className="dist-row" key={status}>
                  <span
                    className="dist-label mono"
                    style={{ color: statusColor(status) }}
                  >
                    {statusLabel(status)}
                  </span>
                  <div className="dist-bar-wrap">
                    <div
                      className="dist-bar"
                      style={{
                        width: `${pct}%`,
                        background: statusColor(status),
                      }}
                    />
                  </div>
                  <span className="dist-count">{count}</span>
                </div>
              )
            })}
        </div>
      )}

      {/* 추정 지표 면책 고지 */}
      <div
        style={{
          marginTop: 12,
          fontSize: 9,
          color: 'var(--text-dim)',
          lineHeight: 1.4,
          borderTop: '1px solid var(--border)',
          paddingTop: 8,
        }}
      >
        <span style={{ fontWeight: 600, color: 'var(--text-muted)' }}>추정 지표 가정</span>{' '}
        {data.savings_assumption}
      </div>
    </div>
  )
}
