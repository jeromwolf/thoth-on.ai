// ============================================================
// API Response Types — mirrors api/schemas.py
// ============================================================

export interface SignalSummary {
  type: string
  weight: number | null
  detail: Record<string, unknown>
}

export interface CaseListItem {
  case_id: string
  customer_id: string
  score: number
  status: string
  ring_id: string
  assignee: string
  signal_summary: string[]
}

export interface CaseListResponse {
  total: number
  count: number
  offset: number
  threshold: number
  items: CaseListItem[]
}

export interface PathNode {
  id: string
  type: string
  label: string
}

export interface PathEdge {
  source: string
  target: string
  type: string
}

export interface EvidencePath {
  signal_type: string
  weight: number | null
  label: string
  nodes: PathNode[]
  edges: PathEdge[]
  entities: string[]
}

export interface GroundingResult {
  grounded: boolean
  cited_entities: string[]
  hallucinated: string[]
  num_known: number
}

export interface Explanation {
  text: string
  provider: string
  accepted: boolean
  grounding: GroundingResult
}

export interface HistoryEntry {
  from_status: string
  to_status: string
  actor: string
  note: string
  ts: string
}

export interface VerdictEntry {
  label: string
  actor: string
  comment: string
  ts: string
}

export interface CaseDetailResponse {
  case_id: string
  customer_id: string
  score: number
  status: string
  ring_id: string
  assignee: string
  created_at: string
  updated_at: string
  signals: SignalSummary[]
  paths: EvidencePath[]
  explanation: Explanation
  history: HistoryEntry[]
  verdicts: VerdictEntry[]
}

// Graph types — vis-network compatible
export interface GraphNode {
  id: string
  label: string
  group: string
  title?: string
  suspicious: boolean
}

export interface GraphEdge {
  from: string
  to: string
  label: string
  suspicious: boolean
}

export interface GraphResponse {
  customer_id: string
  center: string
  ring_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  node_count: number
  edge_count: number
}

export interface KpiResponse {
  // 실측 지표
  total_cases: number
  status_distribution: Record<string, number>
  suspected_rings: number
  high_risk_cases: number
  fraud_verdicts: number
  avg_score: number
  avg_high_risk_score: number
  avg_low_risk_score: number
  score_separation: number
  threshold: number
  // 추정 지표 (가정 명시)
  daily_throughput_estimate: number
  detection_rate_pct: number
  estimated_savings_krw: number
  savings_assumption: string
}

// Retrain types — mirrors /detection/retrain response schema
export interface RetrainProvenance {
  n_total: number
  n_feedback: number
  n_overrides: number
  n_agree: number
  n_base: number
}

export interface RetrainMetrics {
  recall: number
  precision: number
  f1: number
  fpr: number
  auc: number
  tp: number
  fp: number
  fn: number
  tn: number
}

export interface RetrainResponse {
  model_kind: string
  n_folds: number
  provenance: RetrainProvenance
  baseline: RetrainMetrics
  feedback: RetrainMetrics
  delta_auc: number
  delta_f1: number
  note: string
  // persistence fields (present when persist=true)
  persisted?: boolean
  model_path?: string | null
  trained_at?: string | null
}

export interface ActiveModel {
  active: boolean
  trained_at?: string
  model_kind?: string
  n_samples?: number
  n_positive?: number
  feature_count?: number
}

// Rescore types — mirrors /detection/rescore response schema
export interface RescoreSummary {
  n_flagged: number
  n_created: number
  n_updated: number
  n_unchanged: number
  used_ml: boolean
}

// UI state types
export type View = 'queue' | 'detail' | 'graph' | 'kpi' | 'retrain'

export interface AppState {
  view: View
  selectedCaseId: string | null
  selectedCustomerId: string | null
}
