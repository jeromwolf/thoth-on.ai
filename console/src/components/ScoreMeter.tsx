import { scoreColor, scoreLabel } from '../utils/score'

interface Props {
  score: number
  size?: 'sm' | 'md' | 'lg'
}

export function ScoreMeter({ score, size = 'md' }: Props) {
  const color = scoreColor(score)
  const label = scoreLabel(score)

  if (size === 'lg') {
    return (
      <div className="panel-score-block">
        <div className="panel-score-num" style={{ color }}>
          {score.toFixed(1)}
        </div>
        <div className="panel-score-label">{label}</div>
      </div>
    )
  }

  const fontSize = size === 'sm' ? 13 : 15
  return (
    <span
      className="score-badge"
      style={{ color, fontSize }}
      title={`리스크 점수: ${score}`}
    >
      {score.toFixed(1)}
    </span>
  )
}
