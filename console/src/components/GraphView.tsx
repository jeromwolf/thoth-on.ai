import { useEffect, useRef, useState } from 'react'
import { Network, DataSet } from 'vis-network/standalone'
import { fetchCustomerGraph } from '../api/endpoints'
import type { GraphResponse } from '../types'

// Node group → color mapping
const GROUP_COLORS: Record<string, { background: string; border: string; font: string }> = {
  Customer:    { background: '#1a3a5c', border: '#2d7dd2', font: '#7fc0ff' },
  Account:     { background: '#1a4030', border: '#2eb872', font: '#6fffb0' },
  RepairShop:  { background: '#3a2a00', border: '#f09438', font: '#ffc470' },
  Hospital:    { background: '#3a1a2a', border: '#e060a0', font: '#ffaacc' },
  Claim:       { background: '#2a1a3a', border: '#9b6dff', font: '#c9aaff' },
  Policy:      { background: '#1a2a3a', border: '#3b9ede', font: '#8ad0ff' },
  default:     { background: '#1a2030', border: '#253547', font: '#8a9bb0' },
}

// Suspicious node / ring member appearance
const SUSPICIOUS_NODE = {
  background: '#2a0a0a',
  border: '#f03e3e',
  font: '#ff7070',
  shadow: { enabled: true, color: 'rgba(240,62,62,0.5)', size: 12, x: 0, y: 0 },
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

const LEGEND_ITEMS = [
  { label: 'Customer', color: '#2d7dd2' },
  { label: 'Account', color: '#2eb872' },
  { label: 'RepairShop', color: '#f09438' },
  { label: 'Hospital', color: '#e060a0' },
  { label: 'Claim', color: '#9b6dff' },
  { label: 'Policy', color: '#3b9ede' },
  { label: '의심/링', color: '#f03e3e', suspicious: true },
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

    // Destroy previous instance
    if (networkRef.current) {
      networkRef.current.destroy()
      networkRef.current = null
    }

    // Build vis-network nodes
    const nodes = new DataSet(
      graphData.nodes.map((n) => {
        const colorSpec = getNodeColor(n.group, n.suspicious)
        return {
          id: n.id,
          label: n.label,
          group: n.group,
          title: n.title ?? `${n.group}: ${n.id}`,
          color: colorSpec,
          font: {
            color: n.suspicious
              ? '#ff7070'
              : (GROUP_COLORS[n.group]?.font ?? '#8a9bb0'),
            size: 11,
            face: 'IBM Plex Mono, monospace',
          },
          borderWidth: n.suspicious ? 2.5 : 1.5,
          size: n.suspicious ? 22 : (n.id === graphData.center ? 28 : 16),
          // Center node gets special star shape
          shape: n.id === graphData.center ? 'diamond' : n.suspicious ? 'dot' : 'dot',
          shadow: n.suspicious
            ? { enabled: true, color: 'rgba(240,62,62,0.45)', size: 14, x: 0, y: 0 }
            : undefined,
        }
      })
    )

    // Build vis-network edges
    const edges = new DataSet(
      graphData.edges.map((e, i) => ({
        id: `edge-${i}`,
        from: e.from,
        to: e.to,
        label: e.label,
        color: {
          color: e.suspicious ? '#f03e3e' : '#253547',
          highlight: e.suspicious ? '#ff7070' : '#2d7dd2',
          hover: e.suspicious ? '#ff7070' : '#3b9ede',
          opacity: e.suspicious ? 0.9 : 0.6,
        },
        width: e.suspicious ? 2.5 : 1,
        dashes: !e.suspicious ? false : [6, 3],
        font: {
          color: e.suspicious ? '#f03e3e' : '#4a5c6e',
          size: 9,
          face: 'IBM Plex Mono, monospace',
          align: 'middle',
          strokeWidth: 2,
          strokeColor: '#080b0f',
        },
        arrows: { to: { enabled: true, scaleFactor: 0.5, type: 'arrow' } },
        smooth: { enabled: true, type: 'dynamic', roundness: 0.3 },
        shadow: e.suspicious
          ? { enabled: true, color: 'rgba(240,62,62,0.3)', size: 6, x: 0, y: 0 }
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
        stabilization: {
          enabled: true,
          iterations: 200,
          updateInterval: 25,
          fit: true,
        },
      },
      interaction: {
        hover: true,
        tooltipDelay: 150,
        zoomView: true,
        dragView: true,
        multiselect: false,
      },
      layout: {
        improvedLayout: true,
        randomSeed: 42,
      },
      nodes: {
        shape: 'dot',
        scaling: { min: 10, max: 30 },
      },
      edges: {
        scaling: { min: 1, max: 4 },
      },
    }

    const network = new Network(containerRef.current, { nodes, edges }, options)
    networkRef.current = network

    // After stabilization, fit the view
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
      <div className="graph-toolbar">
        <span className="mono text-muted" style={{ fontSize: 10, letterSpacing: '0.08em' }}>
          고객 관계망
        </span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--accent-bright)' }}>
          {customerId}
        </span>
        {graphData?.ring_id && (
          <span className="badge badge-ring">ring·{graphData.ring_id}</span>
        )}
        {graphData && (
          <span className="text-muted mono" style={{ fontSize: 10 }}>
            {graphData.node_count}노드 · {graphData.edge_count}엣지
          </span>
        )}
        {loading && <span className="loading-spinner" style={{ width: 14, height: 14 }} />}
        <div style={{ marginLeft: 'auto' }}>
          <button
            className="btn btn-xs btn-ghost"
            onClick={() => networkRef.current?.fit({ animation: true })}
          >
            맞춤
          </button>
        </div>
      </div>

      {/* Legend */}
      <div style={{ padding: '6px 16px', background: 'var(--bg-base)', borderBottom: '1px solid var(--border-dim)' }}>
        <div className="graph-legend">
          {LEGEND_ITEMS.map((item) => (
            <div key={item.label} className="legend-item">
              <span
                className={`legend-dot${item.suspicious ? ' suspicious' : ''}`}
                style={{ background: item.color }}
              />
              {item.label}
            </div>
          ))}
        </div>
      </div>

      <div className="graph-container">
        {error && (
          <div style={{ padding: 20 }}>
            <div className="error-banner">{error}</div>
          </div>
        )}
        <div ref={containerRef} id="vis-network" style={{ width: '100%', height: '100%' }} />
      </div>
    </div>
  )
}
