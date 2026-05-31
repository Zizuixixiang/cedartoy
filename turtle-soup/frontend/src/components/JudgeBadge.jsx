const JUDGMENT_STYLES = {
  yes: ['是', 'judgment-yes'],
  no: ['不是', 'judgment-no'],
  partial: ['是也不是', 'judgment-partial'],
  unrelated: ['不相干', 'judgment-unrelated'],
}

export default function JudgeBadge({ value }) {
  if (!value || value === 'game_over' || value === 'auto_hint') return null
  const [label, cls] = JUDGMENT_STYLES[value] || [value, 'judgment-other']
  return <span className={`log-judgment ${cls}`}>&gt; {label}</span>
}
