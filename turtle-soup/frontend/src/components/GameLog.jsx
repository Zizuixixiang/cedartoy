import JudgeBadge from './JudgeBadge.jsx'

export default function GameLog({ logs, onReport }) {
  return (
    <div className="log-list">
      {logs.map((log) => (
        <div className={`log-row ${log.type}`} key={log.id} onContextMenu={(e) => { e.preventDefault(); onReport?.(log) }}>
          <div className="log-meta">{log.username || (log.player_id ? `游客${log.player_id}` : '系统')} · {log.type}</div>
          <div className="log-content">{log.content}</div>
          <JudgeBadge value={log.judgment} />
        </div>
      ))}
    </div>
  )
}
