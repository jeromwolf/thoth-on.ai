// Score → color utility (CSS custom property refs)
export function scoreColor(score: number): string {
  if (score >= 80) return 'var(--c-danger)'
  if (score >= 60) return 'var(--c-warning)'
  if (score >= 40) return 'var(--c-caution)'
  return 'var(--c-safe)'
}

export function scoreLabel(score: number): string {
  if (score >= 80) return '위험'
  if (score >= 60) return '높음'
  if (score >= 40) return '보통'
  return '낮음'
}

// Soft tinted background for score badges (light theme)
export function scoreBg(score: number): string {
  if (score >= 80) return 'var(--c-danger-bg)'
  if (score >= 60) return 'var(--c-warning-bg)'
  if (score >= 40) return 'var(--c-caution-bg)'
  return 'var(--c-safe-bg)'
}

export function statusColor(status: string): string {
  switch (status.toUpperCase()) {
    case 'UNASSIGNED': return 'var(--c-badge-unassigned)'
    case 'INVESTIGATING': return 'var(--c-badge-investigating)'
    case 'CLOSED_FRAUD': return 'var(--c-danger)'
    case 'CLOSED_NORMAL': return 'var(--c-safe)'
    case 'HOLD': return 'var(--c-caution)'
    default: return 'var(--c-muted)'
  }
}

export function statusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case 'UNASSIGNED': return '미배정'
    case 'INVESTIGATING': return '조사중'
    case 'CLOSED_FRAUD': return '사기확정'
    case 'CLOSED_NORMAL': return '정상종결'
    case 'HOLD': return '보류'
    default: return status
  }
}

export function formatTs(ts: string): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('ko-KR', {
      year: '2-digit', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ts
  }
}
