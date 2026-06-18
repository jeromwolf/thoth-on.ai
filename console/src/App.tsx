import { useState } from 'react'
import { CaseQueue } from './components/CaseQueue'
import { CaseDetail } from './components/CaseDetail'
import { GraphView } from './components/GraphView'
import { KpiDashboard } from './components/KpiDashboard'
import { RetrainPanel } from './components/RetrainPanel'
import { LlmStatusBadge } from './components/LlmStatusBadge'
import type { CaseListItem, View } from './types'

const X_ROLE = (import.meta.env.VITE_X_ROLE as string | undefined) ?? 'FRAUD_ANALYST'
const ROLE_LABELS: Record<string, string> = {
  FRAUD_ANALYST: '사기분석관',
  SIU_LEAD: 'SIU 팀장',
  ADMIN: '관리자',
}

function IconQueue() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <rect x="2" y="3" width="12" height="2.4" rx="1.2" />
      <rect x="2" y="7" width="12" height="2.4" rx="1.2" />
      <rect x="2" y="11" width="7" height="2.4" rx="1.2" />
    </svg>
  )
}

function IconGraph() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
      <circle cx="8" cy="8" r="2.2" />
      <circle cx="2.5" cy="3.5" r="1.6" />
      <circle cx="13.5" cy="3.5" r="1.6" />
      <circle cx="3" cy="13" r="1.6" />
      <circle cx="13" cy="13" r="1.6" />
      <line x1="6.2" y1="6.6" x2="3.7" y2="4.7" />
      <line x1="9.8" y1="6.6" x2="12.3" y2="4.7" />
      <line x1="6.5" y1="9.7" x2="4.2" y2="11.7" />
      <line x1="9.5" y1="9.7" x2="11.8" y2="11.7" />
    </svg>
  )
}

function IconKpi() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="2,11 6,7 9,9 14,4" />
      <line x1="2" y1="14" x2="14" y2="14" />
    </svg>
  )
}

function IconRetrain() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13.5 8A5.5 5.5 0 1 1 8 2.5" />
      <polyline points="11,1 13.5,2.5 12,5" />
    </svg>
  )
}

function IconSearch() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="7" cy="7" r="4.5" />
      <line x1="10.5" y1="10.5" x2="14" y2="14" strokeLinecap="round" />
    </svg>
  )
}

function ThothLogo() {
  return (
    <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
      <rect x="1" y="1" width="20" height="20" rx="4" stroke="#2c5eed" strokeWidth="1.6" />
      <path d="M6 7h10M11 7v8" stroke="#2c5eed" strokeWidth="1.9" strokeLinecap="round" />
      <path d="M7 15h8" stroke="#c8182e" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

type MainView = 'detail' | 'graph' | 'kpi' | 'retrain' | 'empty'

export default function App() {
  const [selectedCase, setSelectedCase] = useState<CaseListItem | null>(null)
  const [graphCustomerId, setGraphCustomerId] = useState<string | null>(null)
  const [mainView, setMainView] = useState<MainView>('empty')
  const [activeNav, setActiveNav] = useState<View>('queue')
  const [search, setSearch] = useState('')

  function handleCaseSelect(item: CaseListItem) {
    setSelectedCase(item)
    setMainView('detail')
    setActiveNav('queue')
  }

  function handleOpenGraph(customerId: string) {
    setGraphCustomerId(customerId)
    setMainView('graph')
    setActiveNav('graph' as View)
  }

  function navigateTo(view: View) {
    setActiveNav(view)
    if (view === 'kpi') {
      setMainView('kpi')
    } else if (view === 'retrain') {
      setMainView('retrain')
    } else if (view === 'graph') {
      if (graphCustomerId) setMainView('graph')
      else if (selectedCase) {
        setGraphCustomerId(selectedCase.customer_id)
        setMainView('graph')
      }
    } else {
      if (selectedCase) setMainView('detail')
      else setMainView('empty')
    }
  }

  const roleLabel = ROLE_LABELS[X_ROLE] ?? X_ROLE
  const roleInitial = roleLabel.charAt(0)

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="app-header">
        <div className="header-brand">
          <div className="header-logo">
            <ThothLogo />
          </div>
          <div>
            <div className="header-title">THOTH-ON</div>
            <div className="header-subtitle">보험사기 탐지 콘솔</div>
          </div>
        </div>

        <div className="header-divider" />

        <label className="header-search">
          <IconSearch />
          <input
            type="text"
            placeholder="사건 ID · 고객 ID 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="사건 검색"
          />
        </label>

        <div className="header-spacer" />

        <nav className="header-nav" aria-label="주요 메뉴">
          <button
            className={`nav-btn${activeNav === 'queue' ? ' active' : ''}`}
            onClick={() => navigateTo('queue')}
          >
            <IconQueue />
            의심 사건
          </button>
          <button
            className={`nav-btn${activeNav === 'graph' ? ' active' : ''}`}
            onClick={() => navigateTo('graph' as View)}
            disabled={!graphCustomerId && !selectedCase}
            title={!graphCustomerId && !selectedCase ? '먼저 사건을 선택하세요' : undefined}
          >
            <IconGraph />
            관계망
          </button>
          <button
            className={`nav-btn${activeNav === 'kpi' ? ' active' : ''}`}
            onClick={() => navigateTo('kpi')}
          >
            <IconKpi />
            현황판
          </button>
          <button
            className={`nav-btn${activeNav === 'retrain' ? ' active' : ''}`}
            onClick={() => navigateTo('retrain')}
          >
            <IconRetrain />
            재학습
          </button>
        </nav>

        <div className="header-divider" />

        <div className="header-status">
          <span className="api-status" title="API 서버 연결됨">
            <span className="status-dot" />
            연결됨
          </span>
          <LlmStatusBadge />
          <div className="role-chip" title={`역할: ${X_ROLE}`}>
            <span className="role-avatar">{roleInitial}</span>
            {roleLabel}
          </div>
        </div>
      </header>

      {/* Sidebar — Case Queue */}
      <CaseQueue
        selectedCaseId={selectedCase?.case_id ?? null}
        onSelect={handleCaseSelect}
        search={search}
      />

      {/* Main content */}
      <main className="main-content">
        {mainView === 'empty' && (
          <div className="empty-state">
            <div className="empty-illus">
              <svg width="32" height="32" viewBox="0 0 22 22" fill="none">
                <rect x="1" y="1" width="20" height="20" rx="4" stroke="currentColor" strokeWidth="1.6" />
                <path d="M6 7h10M11 7v8" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
              </svg>
            </div>
            <span className="empty-title">사건을 선택하세요</span>
            <span className="empty-text">
              왼쪽 목록에서 의심 사건을 클릭하면 위험 점수, 소명 근거,
              기여 신호 등 상세 내용을 확인할 수 있습니다.
            </span>
          </div>
        )}

        {mainView === 'detail' && selectedCase && (
          <CaseDetail
            caseId={selectedCase.case_id}
            onOpenGraph={handleOpenGraph}
          />
        )}

        {mainView === 'graph' && graphCustomerId && (
          <GraphView customerId={graphCustomerId} />
        )}

        {mainView === 'kpi' && <KpiDashboard />}

        {mainView === 'retrain' && <RetrainPanel />}
      </main>
    </div>
  )
}
