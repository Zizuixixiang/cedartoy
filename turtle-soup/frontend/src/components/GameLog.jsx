import JudgeBadge from './JudgeBadge.jsx'

function formatLogTime(createdAt) {
  if (!createdAt) return ''
  const raw = String(createdAt).trim()
  const normalized = raw.includes('T') ? raw : `${raw.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function isResolved(log) {
  return Number(log.resolved) === 1
}

function HintBanner({ log, onRespond, busy }) {
  const resolved = isResolved(log)
  const accepted = log.hint_accepted
  const hintText = log.hint_text || ''
  let body = '系统提供了一条线索，是否接受查看？'
  if (resolved && accepted === false) {
    body = '已拒绝该提示'
  } else if (resolved && accepted && hintText) {
    body = hintText
  }

  return (
    <div className={`log-hint-banner hint-offer${resolved ? ' readonly' : ''}`} role="region" aria-label="请求提示">
      <div className="log-hint-label">&gt; 【请求提示】</div>
      <p>{body}</p>
      {!resolved && (
        <div className="hint-actions">
          <button type="button" disabled={busy} onClick={() => onRespond(log.id, false)}>拒绝</button>
          <button type="button" className="pixel-primary" disabled={busy} onClick={() => onRespond(log.id, true)}>接受</button>
        </div>
      )}
      {resolved && <span className="hint-resolved-tag">已处理</span>}
    </div>
  )
}

function AutoHintBanner({ log }) {
  return (
    <div className="log-hint-banner auto" role="region" aria-label="线索公布">
      <div className="log-hint-label">&gt; 【线索公布】</div>
      <p>{log.hint_text || log.content}</p>
    </div>
  )
}

function GameOverReveal({ content }) {
  return (
    <div className="log-game-over" role="region" aria-label="汤底揭晓">
      <div className="log-game-over-label">&gt; 汤底揭晓</div>
      <p>{content}</p>
    </div>
  )
}

function sortLogs(logs) {
  const rows = [...logs].sort((a, b) => Number(a.id) - Number(b.id))
  const main = []
  const reveals = []
  for (const row of rows) {
    if (row.judgment === 'game_over') reveals.push(row)
    else main.push(row)
  }
  return [...main, ...reveals]
}

function systemNoticeContent(content) {
  const text = String(content || '').trim()
  if (text.startsWith('【系统提示】')) return text
  if (/^(系统|裁判)开小差了/.test(text)) {
    return text.replace(/^(系统|裁判)/, '【系统提示】系统')
  }
  return ''
}

export default function GameLog({ logs, onReport, onHintRespond, hintBusy }) {
  const ordered = sortLogs(logs)

  return (
    <div className="session-log-body">
      {ordered.map((log) => {
        if (log.judgment === 'game_over') {
          return <GameOverReveal key={`reveal-${log.id}`} content={log.content} />
        }
        if (log.type === 'hint_offer') {
          return (
            <HintBanner
              key={`hint-${log.id}`}
              log={log}
              busy={hintBusy}
              onRespond={onHintRespond}
            />
          )
        }
        if (log.type === 'auto_hint' || log.judgment === 'auto_hint') {
          return <AutoHintBanner key={`auto-${log.id}`} log={log} />
        }
        if (log.type === 'hint_accept' || log.type === 'hint_reject') {
          return null
        }
        if (!['ask', 'guess', 'system'].includes(log.type)) {
          return null
        }
        if (log.type === 'system' && log.judgment === 'game_over') {
          return null
        }
        const notice = systemNoticeContent(log.content)
        const time = formatLogTime(log.created_at)
        const name = log.username || (log.player_id ? `游客${log.player_id}` : '系统')
        const prefix = log.type === 'guess' ? '猜测' : log.type === 'system' ? '系统' : name
        return (
          <div
            className={`session-log-line ${log.type}${notice ? ' system-notice' : ''}`}
            key={log.id}
            onContextMenu={(event) => {
              event.preventDefault()
              onReport?.(log)
            }}
          >
            <span className="log-time">{time}</span>
            {!notice && <JudgeBadge value={log.judgment} />}
            <span className="log-text">
              {notice || (log.type === 'system' ? '' : `${prefix}：`)}
              {notice ? '' : log.content}
            </span>
          </div>
        )
      })}
    </div>
  )
}
