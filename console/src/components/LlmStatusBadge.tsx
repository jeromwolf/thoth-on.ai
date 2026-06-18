import { useState, useEffect } from 'react'
import { getLlmStatus } from '../api/endpoints'
import type { LlmStatus } from '../types'

type BadgeState = 'loading' | 'live' | 'mock' | 'fallback' | 'error'

function resolveBadgeState(data: LlmStatus): BadgeState {
  if (data.provider === 'mock') return 'mock'
  if (data.fallback_to_mock) return 'fallback'
  return 'live'
}

const DOT_COLORS: Record<BadgeState, string> = {
  loading: '#b0b8c8',
  live: '#22c55e',
  mock: '#94a3b8',
  fallback: '#f59e0b',
  error: '#94a3b8',
}

const LABELS: Record<BadgeState, string> = {
  loading: 'LLM 확인 중',
  live: '',       // filled dynamically
  mock: 'Mock 소명(결정적)',
  fallback: 'Mock fallback',
  error: '',
}

export function LlmStatusBadge() {
  const [data, setData] = useState<LlmStatus | null>(null)
  const [badgeState, setBadgeState] = useState<BadgeState>('loading')

  useEffect(() => {
    getLlmStatus()
      .then((d) => {
        setData(d)
        setBadgeState(resolveBadgeState(d))
      })
      .catch(() => {
        setBadgeState('error')
      })
  }, [])

  // error → silent hide
  if (badgeState === 'error') return null

  const dotColor = DOT_COLORS[badgeState]

  let label: string
  if (badgeState === 'live' && data) {
    const model = data.configured_model ?? ''
    label = model ? `${data.provider} · ${model}` : data.provider
  } else {
    label = LABELS[badgeState]
  }

  const tooltipText = data?.note ?? ''

  return (
    <span
      className="llm-status-badge"
      title={tooltipText}
      style={{ '--llm-dot': dotColor } as React.CSSProperties}
    >
      <span className="llm-status-dot" />
      <span className="llm-status-label">{label}</span>
    </span>
  )
}
