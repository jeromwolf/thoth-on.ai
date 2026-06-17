import { useEffect, useRef, useState } from 'react'
import { Network, DataSet } from 'vis-network/standalone'
import { fetchCustomerGraph } from '../api/endpoints'
import type { GraphResponse } from '../types'

// Node group → light-theme color mapping
const GROUP_COLORS: Record<string, { background: string; border: string; font: string }> = {
  Customer:    { background: '#e8eeff', border: '#2c5eed', font: '#1d47c4' },
  Account:     { background: '#dcf5ec', border: '#1a7a52', font: '#15623f' },
  RepairShop:  { background: '#fef0e0', border: '#b04800', font: '#8a3800' },
  Hospital:    { background: '#fbe6f0', border: '#c43d82', font: '#9c2e66' },
  Claim:       { background: '#ede6fb', border: '#6d3fd4', font: '#542fa8' },
  Policy:      { background: '#e0f5f8', border: '#0d7a8c', font: '#0a5e6c' },
  default:     { background: '#f1f4f9', border: '#8898b0', font: '#5a6a85' },
}

const SUSPICIOUS_NODE = {
  background: '#fde8ea',
  border: '#c8182e',
  font: '#9c1224',
  shadow: { enabled: true, color: 'rgba(200,24,46,0.30)', size: 16, x: 0, y: 0 },
}

function getNodeColor(group: string, suspicious: boolean) {
  if (suspicious) return SUSPICIOUS_NODE
  const c = GROUP_COLORS[group] ?? GROUP_COLORS['default']
  return {
    background: c.background,
    border: c.border,
    font: { color: c.font },
  }
}

const GROUP_LABELS_KR: Record<string, string> = {
  Customer: '고객',
  Account: '계좌',
  RepairShop: '정비소',
  Hospital: '병원',
  Claim: '청구',
  Policy: '보험계약',
}

const LEGEND_ITEMS = [
  { label: '고객', color: '#2c5eed' },
  { label: '계좌', color: '#1a7a52' },
  { label: '정비소', color: '#b04800' },
  { label: '병원', color: '#c43d82' },
  { label: '청구', color: '#6d3fd4' },
  { label: '보험계약', color: '#0d7a8c' },
  { label: '의심 / 조직', color: '#c8182e', suspicious: true },
]

interface Props {
  customerId: string
}

export function GraphView({ customerId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const [graphData, setGraphData] = useState<GraphResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!customerId) return
    setLoading(true)
    setError(null)
    fetchCustomerGraph(customerId)
      .then((d) => setGraphData(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '그래프 로드 실패'))
      .finally(() => setLoading(false))
  }, [customerId])

  useEffect(() => {
    if (!graphData || !containerRef.current) return

    if (networkRef.current) {
      networkRef.current.destroy()
      networkRef.current = null
    }

    const nodes = new DataSet(
      graphData.nodes.map((n) => {
        const colorSpec = getNodeColor(n.group, n.suspicious)
        const groupKr = GROUP_LABELS_KR[n.group] ?? n.group
        return {
          id: n.id,
          label: n.label,
          group: n.group,
          title: n.title ?? `${groupKr}: ${n.id}`,
          color: colorSpec,
          font: {
            color: n.suspicious ? '#9c1224' : (GROUP_COLORS[n.group]?.font ?? '#5a6a85'),
            size: 12,
            face: 'IBM Plex Mono, monospace',
          },
          borderWidth: n.suspicious ? 3 : 2,
          size: n.suspicious ? 24 : (n.id === graphData.center ? 30 : 18),
          shape: n.id === graphData.center ? 'diamond' : 'dot',
          shadow: n.suspicious
            ? { enabled: true, color: 'rgba(200,24,46,0.28)', size: 16, x: 0, y: 0 }
            : { enabled: true, color: 'rgba(26,34,51,0.10)', size: 8, x: 0, y: 2 },
        }
      })
    )

    const edges = new DataSet(
      graphData.edges.map((e, i) => ({
        id: `edge-${i}`,
        from: e.from,
        to: e.to,
        label: e.label,
        color: {
          color: e.suspicious ? '#c8182e' : '#c5cdda',
          highlight: e.suspicious ? '#c8182e' : '#2c5eed',
          hover: e.suspicious ? '#c8182e' : '#2c5eed',
          opacity: e.suspicious ? 0.95 : 0.7,
        },
        width: e.suspicious ? 2.5 : 1.2,
        dashes: e.suspicious ? [6, 3] : false,
        font: {
          color: e.suspicious ? '#c8182e' : '#5a6a85',
          size: 10,
          face: 'IBM Plex Mono, monospace',
          align: 'middle',
          strokeWidth: 4,
          strokeColor: '#ffffff',
        },
        arrows: { to: { enabled: true, scaleFactor: 0.5, type: 'arrow' } },
        smooth: { enabled: true, type: 'dynamic', roundness: 0.3 },
        shadow: e.suspicious
          ? { enabled: true, color: 'rgba(200,24,46,0.20)', size: 6, x: 0, y: 0 }
          : undefined,
      }))
    )

    const options = {
      autoResize: true,
      height: '100%',
      width: '100%',
      physics: {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -80,
          centralGravity: 0.005,
          springLength: 120,
          springConstant: 0.08,
          damping: 0.5,
          avoidOverlap: 0.8,
        },
        stabilization: { enabled: true, iterations: 200, updateInterval: 25, fit: true },
      },
      interaction: {
        hover: true,
        tooltipDelay: 150,
        zoomView: true,
        dragView: true,
        multiselect: false,
      },
      layout: { improvedLayout: true, randomSeed: 42 },
      nodes: { shape: 'dot', scaling: { min: 12, max: 32 } },
      edges: { scaling: { min: 1, max: 4 } },
    }

    const network = new Network(containerRef.current, { nodes, edges }, options)
    networkRef.current = network

    network.once('stabilizationIterationsDone', () => {
      network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } })
    })

    return () => {
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
    }
  }, [graphData])

  return (
    <div className="graph-panel">
      <div className="graph-card">
        <div className="graph-header">
          <span className="graph-header-title">고객 관계망</span>
          <span className="graph-header-sub">{customerId}</span>
          {graphData?.ring_id && (
            <span className="badge badge-ring">의심 조직 {graphData.ring_id}</span>
          )}
          {graphData && (
            <span className="graph-stat">
              노드 {graphData.node_count}개 · 연결 {graphData.edge_count}개
            </span>
          )}
          {loading && <span className="loading-spinner" style={{ width: 16, height: 16 }} />}
          <button
            className="btn btn-ghost btn-sm"
            style={{ marginLeft: 'auto' }}
            onClick={() => networkRef.current?.fit({ animation: true })}
          >
            화면에 맞추기
          </button>
        </div>

        <div className="graph-legend">
          {LEGEND_ITEMS.map((item) => (
            <div key={item.label} className="legend-item">
              <span
                className={`legend-dot${item.suspicious ? ' suspicious' : ''}`}
                style={{ background: item.color, color: item.color }}
              />
              {item.label}
            </div>
          ))}
        </div>

        <div className="graph-container">
          {error && (
            <div style={{ padding: 24 }}>
              <div className="banner banner-error">{error}</div>
            </div>
          )}
          <div ref={containerRef} id="vis-network" style={{ width: '100%', height: '100%' }} />
        </div>
      </div>
    </div>
  )
}
