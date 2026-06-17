import { scoreColor, scoreLabel, scoreBg } from '../utils/score'

interface Props {
  score: number
  size?: 'sm' | 'md' | 'lg'
}

export function ScoreMeter({ score, size = 'md' }: Props) {
  const color = scoreColor(score)
  const bg = scoreBg(score)
  const label = scoreLabel(score)

  if (size === 'lg') {
    return (
      <div className="score-block" style={{ background: bg }}>
        <div className="score-block-num" style={{ color }}>
          {score.toFixed(0)}
        </div>
        <div className="score-block-label" style={{ color }}>
          {label}
        </div>
        <div className="score-block-cap">위험 점수 / 100</div>
      </div>
    )
  }

  return (
    <span
      className={`score-pill${size === 'sm' ? ' sm' : ''}`}
      style={{ background: bg }}
      title={`위험 점수: ${score} (${label})`}
    >
      <span className="score-num" style={{ color }}>
        {score.toFixed(0)}
      </span>
      <span className="score-tag" style={{ color }}>
        {label}
      </span>
    </span>
  )
}
