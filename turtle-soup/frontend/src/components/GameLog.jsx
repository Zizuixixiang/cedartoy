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

function HintBanner({ log, onRespond, busy, currentPlayerId }) {
  const resolved = isResolved(log)
  const accepted = log.hint_accepted
  const hintText = log.hint_text || ''
  const isMine = Number(log.player_id) === Number(currentPlayerId)
  const requester = log.username || (log.player_id ? `游客${log.player_id}` : '')
  let body = isMine ? '系统提供了一条线索，是否接受查看？' : `${requester || '玩家'}请求了一条提示，等待处理。`
  if (resolved && accepted === false) {
    body = '已拒绝该提示'
  } else if (resolved && accepted && hintText) {
    body = hintText
  }

  return (
    <div className={`log-hint-banner hint-offer${resolved ? ' readonly' : ''}`} role="region" aria-label="请求提示">
      <div className="log-hint-label">&gt; 【请求提示】</div>
      <p>{body}</p>
      {!resolved && isMine && (
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
      <div className="log-hint-label">&gt; 【特殊线索】</div>
      <p>{log.hint_text || log.content}</p>
    </div>
  )
}

function parseGuessContent(content) {
  const lines = String(content || '').split(/\r?\n/)
  const tail = lines[lines.length - 1]?.trim() || ''
  const scoreMatch = tail.match(/^还原度[:：]\s*(\d+)%?$/)
  if (!scoreMatch) return { guess: String(content || '').trim(), score: '' }
  return {
    guess: lines.slice(0, -1).join('\n').trim(),
    score: `${scoreMatch[1]}%`,
  }
}

function GuessCard({ log, time }) {
  const name = log.username || (log.player_id ? `游客${log.player_id}` : '玩家')
  const parsed = parseGuessContent(log.content)
  return (
    <div className="log-guess-card" role="article" aria-label="玩家猜测">
      <div className="log-card-meta">
        <span>{time}</span>
        <strong>{name}</strong>
      </div>
      <div className="log-guess-label">&gt; 猜测汤底</div>
      <p>{parsed.guess || log.content}</p>
    </div>
  )
}

function GuessResultCard({ log, score = '' }) {
  const content = String(log.content || '').trim()
  const parsed = parseGuessContent(content)
  const displayScore = score || parsed.score
  const isCorrect = log.judgment === 'yes'
  const isError = systemNoticeContent(content)
  return (
    <div className={`log-guess-result${isCorrect ? ' correct' : ''}${isError ? ' system-notice' : ''}`} role="region" aria-label="裁判判定">
      <div className="log-guess-result-label">&gt; 裁判判定</div>
      <p>
        {isError || (isCorrect ? '通关' : '未通关，请继续')}
        {displayScore && <span>还原度 {displayScore}</span>}
      </p>
    </div>
  )
}

function GameOverReveal({ content }) {
  const parsed = parseGuessContent(content)
  return (
    <div className="log-game-over" role="region" aria-label="汤底揭晓">
      <div className="log-game-over-label">&gt; 汤底揭晓</div>
      {parsed.score && <div className="log-game-over-score">还原度 {parsed.score}</div>}
      <p>{parsed.guess || content}</p>
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

export default function GameLog({ logs, onReport, onHintRespond, hintBusy, currentPlayerId }) {
  const ordered = sortLogs(logs)

  return (
    <div className="session-log-body">
      {ordered.map((log) => {
        if (log.judgment === 'game_over') {
          return <GameOverReveal key={`reveal-${log.id}`} content={log.content} />
        }
        if (log.judgment === 'guess_result') {
          return <GuessResultCard key={`guess-result-${log.id}`} log={log} />
        }
        if (log.type === 'hint_offer') {
          return (
            <HintBanner
              key={`hint-${log.id}`}
              log={log}
              busy={hintBusy}
              onRespond={onHintRespond}
              currentPlayerId={currentPlayerId}
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
        if (log.type === 'guess') {
          const parsed = parseGuessContent(log.content)
          return (
            <div className="log-guess-group" key={log.id}>
              <GuessCard log={log} time={time} />
              {parsed.score && <GuessResultCard log={log} score={parsed.score} />}
            </div>
          )
        }
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
