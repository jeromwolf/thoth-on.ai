import { useState } from 'react'
import { CaseQueue } from './components/CaseQueue'
import { CaseDetail } from './components/CaseDetail'
import { GraphView } from './components/GraphView'
import { KpiDashboard } from './components/KpiDashboard'
import type { CaseListItem, View } from './types'

// Icon SVG inline — minimal
function IconQueue() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="3" width="12" height="2" rx="1" />
      <rect x="2" y="7" width="12" height="2" rx="1" />
      <rect x="2" y="11" width="7" height="2" rx="1" />
    </svg>
  )
}

function IconGraph() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="2" />
      <circle cx="2" cy="4" r="1.5" />
      <circle cx="14" cy="4" r="1.5" />
      <circle cx="3" cy="13" r="1.5" />
      <circle cx="13" cy="13" r="1.5" />
      <line x1="6.3" y1="6.7" x2="3.4" y2="5.2" />
      <line x1="9.7" y1="6.7" x2="12.6" y2="5.2" />
      <line x1="6.6" y1="9.6" x2="4.1" y2="11.8" />
      <line x1="9.4" y1="9.6" x2="11.9" y2="11.8" />
    </svg>
  )
}

function IconKpi() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <polyline points="2,12 6,8 9,10 14,5" />
      <line x1="2" y1="14" x2="14" y2="14" />
    </svg>
  )
}

function ThothLogo() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
      <rect x="1" y="1" width="20" height="20" rx="3" stroke="#2d7dd2" strokeWidth="1.5" />
      <path d="M6 7h10M11 7v8" stroke="#2d7dd2" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M7 15h8" stroke="#f03e3e" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

type MainView = 'detail' | 'graph' | 'kpi' | 'empty'

export default function App() {
  const [selectedCase, setSelectedCase] = useState<CaseListItem | null>(null)
  const [graphCustomerId, setGraphCustomerId] = useState<string | null>(null)
  const [mainView, setMainView] = useState<MainView>('empty')
  const [activeNav, setActiveNav] = useState<View>('queue')

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
    } else if (view === 'graph') {
      if (graphCustomerId) setMainView('graph')
      else if (selectedCase) {
        setGraphCustomerId(selectedCase.customer_id)
        setMainView('graph')
      }
    } else {
      // queue — keep detail if case selected
      if (selectedCase) setMainView('detail')
      else setMainView('empty')
    }
  }

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
            <div className="header-subtitle">SIU CONSOLE · 보험사기탐지</div>
          </div>
        </div>

        <div className="header-divider" />

        <nav className="header-nav">
          <button
            className={`nav-btn${activeNav === 'queue' ? ' active' : ''}`}
            onClick={() => navigateTo('queue')}
          >
            <IconQueue />
            케이스 큐
          </button>
          <button
            className={`nav-btn${activeNav === 'graph' ? ' active' : ''}`}
            onClick={() => navigateTo('graph' as View)}
            disabled={!graphCustomerId && !selectedCase}
          >
            <IconGraph />
            관계망
          </button>
          <button
            className={`nav-btn${activeNav === 'kpi' ? ' active' : ''}`}
            onClick={() => navigateTo('kpi')}
          >
            <IconKpi />
            KPI
          </button>
        </nav>

        <div className="header-spacer" />

        <div className="header-status">
          <span className="status-dot" />
          <span>API · 8468</span>
        </div>
      </header>

      {/* Sidebar — always visible */}
      <CaseQueue
        selectedCaseId={selectedCase?.case_id ?? null}
        onSelect={handleCaseSelect}
      />

      {/* Main content */}
      <main className="main-content">
        {mainView === 'empty' && (
          <div className="empty-state">
            <div className="empty-state-icon" style={{ fontSize: 48, color: 'var(--border-bright)' }}>
              <ThothLogo />
            </div>
            <span className="empty-state-text">케이스를 선택하세요</span>
            <span className="text-muted mono" style={{ fontSize: 10 }}>
              좌측 큐에서 케이스 클릭 → 상세 조회
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
      </main>
    </div>
  )
}
