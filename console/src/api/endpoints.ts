import { get, post } from './client'
import type {
  CaseListResponse,
  CaseDetailResponse,
  GraphResponse,
  KpiResponse,
  RetrainResponse,
} from '../types'

export interface AssignRequest {
  assignee: string
  note?: string
}
export interface AssignResponse {
  case_id: string
  assignee: string
  status: string
}
export interface VerdictRequest {
  verdict: 'FRAUD' | 'NORMAL' | 'HOLD'
  comment?: string
}
export interface VerdictResponse {
  case_id: string
  verdict: string
  status: string
  recorded: boolean
}

export function fetchCases(params: {
  limit?: number
  offset?: number
  threshold?: number
  status?: string
}): Promise<CaseListResponse> {
  const q = new URLSearchParams()
  if (params.limit !== undefined) q.set('limit', String(params.limit))
  if (params.offset !== undefined) q.set('offset', String(params.offset))
  if (params.threshold !== undefined) q.set('threshold', String(params.threshold))
  if (params.status) q.set('status', params.status)
  const qs = q.toString()
  return get<CaseListResponse>(`/cases${qs ? `?${qs}` : ''}`)
}

export function fetchCaseDetail(caseId: string): Promise<CaseDetailResponse> {
  return get<CaseDetailResponse>(`/cases/${encodeURIComponent(caseId)}`)
}

export function assignCase(caseId: string, req: AssignRequest): Promise<AssignResponse> {
  return post<AssignResponse>(`/cases/${encodeURIComponent(caseId)}/assign`, req)
}

export function recordVerdict(caseId: string, req: VerdictRequest): Promise<VerdictResponse> {
  return post<VerdictResponse>(`/cases/${encodeURIComponent(caseId)}/verdict`, req)
}

export function fetchCustomerGraph(customerId: string): Promise<GraphResponse> {
  return get<GraphResponse>(`/graph/customer/${encodeURIComponent(customerId)}`)
}

export function fetchKpi(threshold?: number): Promise<KpiResponse> {
  const q = threshold !== undefined ? `?threshold=${threshold}` : ''
  return get<KpiResponse>(`/kpi${q}`)
}

export interface RetrainRequest {
  model: string
  folds: number
}

export function retrain(model: string, folds: number): Promise<RetrainResponse> {
  return post<RetrainResponse>('/detection/retrain', { model, folds } satisfies RetrainRequest)
}
