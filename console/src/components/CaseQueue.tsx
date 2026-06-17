import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchCases } from '../api/endpoints'
import type { CaseListItem } from '../types'
import { ScoreMeter } from './ScoreMeter'
import { statusColor, statusLabel } from '../utils/score'

interface Props {
  selectedCaseId: string | null
  onSelect: (item: CaseListItem) => void
  search?: string
}

const STATUS_FILTERS = [
  { label: '전체', value: '' },
  { label: '미배정', value: 'UNASSIGNED' },
  { label: '조사중', value: 'INVESTIGATING' },
  { label: '보류', value: 'HOLD' },
  { label: '사기', value: 'CLOSED_FRAUD' },
  { label: '정상', value: 'CLOSED_NORMAL' },
]

export function CaseQueue({ selectedCaseId, onSelect, search = '' }: Props) {
  const [items, setItems] = useState<CaseListItem[]>([])
  const [threshold, setThreshold] = useState(30)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchCases({
        limit: 100,
        offset: 0,
        threshold,
        status: statusFilter || undefined,
      })
      setItems(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : '로드 실패')
    } finally {
      setLoading(false)
    }
  }, [threshold, statusFilter])

  useEffect(() => { void load() }, [load])

  // 점수 높은 순 정렬 + 검색어 필터 (클라이언트)
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filtered = q
      ? items.filter(
          (it) =>
            it.case_id.toLowerCase().includes(q) ||
            it.customer_id.toLowerCase().includes(q),
        )
      : items
    return [...filtered].sort((a, b) => b.score - a.score)
  }, [items, search])

  return (
    <aside className="sidebar">
      <div className="sidebar-toolbar">
        <div className="sidebar-toolbar-row">
          <span className="sidebar-heading">의심 사건</span>
          {loading ? (
            <span className="loading-spinner" style={{ marginLeft: 'auto', width: 16, height: 16 }} />
          ) : (
            <span className="toolbar-count">{visible.length}건</span>
          )}
        </div>
        <div className="toolbar-sub">위험 점수가 높은 순으로 정렬됩니다</div>

        {/* Threshold slider */}
        <div className="threshold-control">
          <div className="threshold-head">
            <span className="field-label">위험 임계치</span>
            <span className="threshold-val">{threshold}점 이상</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            aria-label="위험 임계치"
          />
        </div>

        {/* Status filter chips */}
        <div>
          <span className="field-label" style={{ display: 'block', marginBottom: 8 }}>
            상태 필터
          </span>
          <div className="status-filter">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                className={`filter-chip${statusFilter === f.value ? ' active' : ''}`}
                onClick={() => setStatusFilter(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="banner banner-error" style={{ fontSize: 12 }}>
            {error}
            <button className="btn btn-xs btn-ghost" onClick={() => void load()}>
              재시도
            </button>
          </div>
        )}
      </div>

      <div className="case-list">
        {visible.length === 0 && !loading && (
          <div className="empty-state" style={{ height: 200 }}>
            <div className="empty-illus" style={{ width: 56, height: 56 }}>
              <svg width="24" height="24" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="7" cy="7" r="4.5" />
                <line x1="10.5" y1="10.5" x2="14" y2="14" strokeLinecap="round" />
              </svg>
            </div>
            <span className="empty-title" style={{ fontSize: 14 }}>
              {search.trim() ? '검색 결과 없음' : '해당하는 사건 없음'}
            </span>
            <span className="empty-text" style={{ fontSize: 12 }}>
              {search.trim()
                ? '다른 검색어를 입력해 보세요.'
                : '임계치를 낮추거나 상태 필터를 바꿔 보세요.'}
            </span>
          </div>
        )}
        {visible.map((item) => (
          <CaseRow
            key={item.case_id}
            item={item}
            selected={item.case_id === selectedCaseId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </aside>
  )
}

function CaseRow({
  item,
  selected,
  onSelect,
}: {
  item: CaseListItem
  selected: boolean
  onSelect: (item: CaseListItem) => void
}) {
  const cls = ['case-item', selected ? 'selected' : ''].filter(Boolean).join(' ')

  return (
    <div
      className={cls}
      onClick={() => onSelect(item)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(item)
        }
      }}
    >
      <div className="case-item-top">
        <ScoreMeter score={item.score} size="md" />
        <div className="case-ids">
          <div className="case-id" title={item.case_id}>
            {item.case_id}
          </div>
          <div className="customer-id" title={item.customer_id}>
            고객 · {item.customer_id}
          </div>
        </div>
      </div>
      <div className="case-item-bottom">
        <span className="badge badge-status" style={{ color: statusColor(item.status) }}>
          <span className="badge-dot" />
          {statusLabel(item.status)}
        </span>

        {item.ring_id && (
          <span className="badge badge-ring" title={`의심 조직(링) ID: ${item.ring_id}`}>
            조직 {item.ring_id.slice(0, 6)}
          </span>
        )}

        {item.signal_summary.slice(0, 2).map((sig) => (
          <span key={sig} className="badge badge-signal" title={sig}>
            {sig.replace(/_/g, ' ').toLowerCase()}
          </span>
        ))}

        {item.signal_summary.length > 2 && (
          <span className="badge badge-signal-more">
            +{item.signal_summary.length - 2}
          </span>
        )}
      </div>
    </div>
  )
}
