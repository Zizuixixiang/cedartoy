const DB_TZ = 'Asia/Shanghai'

/** Parse naive DB datetime (stored as Asia/Shanghai wall time). */
export function parseDbDateTime(value) {
  if (!value) return null
  const raw = String(value).trim()
  if (!raw) return null
  if (raw.includes('T')) {
    if (/[zZ]$/.test(raw) || /[+-]\d{2}:?\d{2}$/.test(raw)) return new Date(raw)
    return new Date(`${raw}+08:00`)
  }
  return new Date(`${raw.replace(' ', 'T')}+08:00`)
}

export function formatDbDateTime(value, options = {}) {
  const date = parseDbDateTime(value)
  if (!date || Number.isNaN(date.getTime())) return String(value ?? '')
  return date.toLocaleString('zh-CN', {
    timeZone: DB_TZ,
    hour12: false,
    ...options,
  })
}

export function formatDbTime(value) {
  return formatDbDateTime(value, {
    year: undefined,
    month: undefined,
    day: undefined,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** 日志单行用：仅时分，移动端省宽度 */
export function formatDbLogClock(value) {
  return formatDbDateTime(value, {
    year: undefined,
    month: undefined,
    day: undefined,
    second: undefined,
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function noteAuthor(note) {
  const name = (note?.username || '').trim()
  if (name) return name
  if (note?.player_id) return `游客${note.player_id}`
  return '未知'
}

export function stripSurfaceColorMarkers(value) {
  return String(value || '').replace(/\[\[\s*([^\]|\n]{1,12}?)\s*\|\s*([^\]]{1,200})\]\]/g, '$2')
}

/** 汤名：优先题库 title，自填房无题时用汤面首句 */
export function soupName(room, maxLen = 24) {
  const title = (room?.title || '').trim()
  if (title) return title.length > maxLen ? `${title.slice(0, maxLen)}…` : title
  const text = stripSurfaceColorMarkers(room?.surface).trim()
  if (!text) return '未命名汤'
  const fallback = text.split(/[，。！？,.!?\n]/)[0].trim() || text.slice(0, maxLen)
  return fallback.length > maxLen ? `${fallback.slice(0, maxLen)}…` : fallback
}
