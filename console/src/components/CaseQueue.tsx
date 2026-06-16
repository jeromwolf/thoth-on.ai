import { useState, useEffect, useCallback } from 'react'
import { fetchCases } from '../api/endpoints'
import type { CaseListItem } from '../types'
import { ScoreMeter } from './ScoreMeter'
import { statusColor, statusLabel } from '../utils/score'

interface Props {
  selectedCaseId: string | null
  onSelect: (item: CaseListItem) => void
}

const STATUS_FILTERS = [
  { label: '전체', value: '' },
  { label: '미배정', value: 'UNASSIGNED' },
  { label: '조사중', value: 'INVESTIGATING' },
  { label: '보류', value: 'HOLD' },
  { label: '사기', value: 'CLOSED_FRAUD' },
  { label: '정상', value: 'CLOSED_NORMAL' },
]

export function CaseQueue({ selectedCaseId, onSelect }: Props) {
  const [items, setItems] = useState<CaseListItem[]>([])
  const [total, setTotal] = useState(0)
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
      setTotal(res.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : '로드 실패')
    } finally {
      setLoading(false)
    }
  }, [threshold, statusFilter])

  useEffect(() => { void load() }, [load])

  return (
    <div className="sidebar">
      <div className="sidebar-toolbar">
        <div className="sidebar-toolbar-row">
          <span className="toolbar-label">케이스 큐</span>
          {loading
            ? <span className="loading-spinner" style={{ marginLeft: 'auto', width: 12, height: 12 }} />
            : <span className="toolbar-count">{total}건</span>
          }
        </div>

        {/* Threshold slider */}
        <div className="threshold-control">
          <span className="toolbar-label" style={{ whiteSpace: 'nowrap' }}>임계치</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
          <span className="threshold-val">{threshold}</span>
        </div>

        {/* Status filter chips */}
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

        {error && (
          <div className="error-banner" style={{ fontSize: 10 }}>
            {error}
            <button
              className="btn btn-xs btn-ghost"
              style={{ marginLeft: 'auto' }}
              onClick={() => void load()}
            >
              재시도
            </button>
          </div>
        )}
      </div>

      <div className="case-list">
        {items.length === 0 && !loading && (
          <div className="empty-state" style={{ height: 160 }}>
            <span className="empty-state-text">케이스 없음</span>
          </div>
        )}
        {items.map((item) => (
          <CaseRow
            key={item.case_id}
            item={item}
            selected={item.case_id === selectedCaseId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
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
  const isHighRisk = item.score >= 70
  const cls = [
    'case-item',
    selected ? 'selected' : '',
    isHighRisk ? 'high-risk' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={cls} onClick={() => onSelect(item)}>
      <div className="case-item-top">
        <ScoreMeter score={item.score} size="md" />
        <div className="case-ids">
          <div className="case-id" title={item.case_id}>{item.case_id}</div>
          <div className="customer-id" title={item.customer_id}>
            cust: {item.customer_id}
          </div>
        </div>
      </div>
      <div className="case-item-bottom">
        <span
          className="badge badge-status"
          style={{ color: statusColor(item.status) }}
        >
          {statusLabel(item.status)}
        </span>

        {item.ring_id && (
          <span className="badge badge-ring" title={`링 ID: ${item.ring_id}`}>
            ring·{item.ring_id.slice(0, 6)}
          </span>
        )}

        {item.signal_summary.slice(0, 2).map((sig) => (
          <span key={sig} className="badge badge-signal" title={sig}>
            {sig.replace(/_/g, ' ').toLowerCase()}
          </span>
        ))}

        {item.signal_summary.length > 2 && (
          <span className="badge badge-signal">
            +{item.signal_summary.length - 2}
          </span>
        )}
      </div>
    </div>
  )
}
