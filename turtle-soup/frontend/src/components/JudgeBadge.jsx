const map = {
  yes: ['是', 'badge yes'],
  no: ['否', 'badge no'],
  unrelated: ['不相关', 'badge unrelated'],
  partial: ['是也不是', 'badge partial'],
}

export default function JudgeBadge({ value }) {
  if (!value) return null
  const [label, cls] = map[value] || [value, 'badge']
  return <span className={cls}>{label}</span>
}
