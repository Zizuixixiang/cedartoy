import { useEffect, useState } from 'react'
import { api } from '../api'
import { formatDbDateTime, soupName } from '../utils/display.js'

const STATUS_LABELS = {
  waiting: '等待中',
  playing: '进行中',
  finished: '已结束',
}

export function HistoryContent({ data, activeId, onActiveId }) {
  const subjects = data?.subjects || []
  const active = subjects.find((subject) => subject.id === activeId) || subjects[0]
  if (!active) return <p className="history-empty">暂无历史数据。</p>
  const stats = active.stats || {}
  return (
    <>
      <div className="history-tabs" role="tablist" aria-label="选择账号">
        {subjects.map((subject) => (
          <button
            type="button"
            role="tab"
            aria-selected={subject.id === active.id}
            className={subject.id === active.id ? 'active' : ''}
            key={subject.id}
            onClick={() => onActiveId(subject.id)}
          >
            {subject.label}
          </button>
        ))}
      </div>
      <div className="history-summary">{active.username} · 海龟汤</div>
      <div className="history-stats" aria-label="海龟汤统计">
        <div><span>全部对局</span><b>{stats.total_games || 0}</b></div>
        <div><span>答出汤底</span><b>{stats.win_count || 0}</b></div>
        <div><span>累计提问</span><b>{stats.ask_count || 0}</b></div>
      </div>
      <div className="history-section-head">
        <h3>最近对局</h3>
        <span>最多显示 30 局</span>
      </div>
      {active.rooms?.length > 0 ? (
        <div className="history-room-list">
          {active.rooms.map((room) => {
            const status = STATUS_LABELS[room.status] || room.status || '未知'
            const result = room.is_winner ? '答出汤底' : status
            return (
              <article className="history-room-card" key={room.id}>
                <div className="history-room-head">
                  <div>
                    <h4>{soupName(room)}</h4>
                    <span>房间 #{room.id}</span>
                  </div>
                  <span className={`history-status ${room.is_winner ? 'won' : room.status || ''}`}>{result}</span>
                </div>
                <div className="history-room-meta">
                  <span>{formatDbDateTime(room.last_active_at || room.finished_at || room.created_at)}</span>
                  {room.is_creator ? <span>房主</span> : <span>参与</span>}
                  <span>提问 {room.ask_count || 0}</span>
                  {room.guess_count ? <span>猜测 {room.guess_count}</span> : null}
                  <span>{room.participant_count || 0} 人参与</span>
                  {room.winner_name && !room.is_winner ? <span>答出：{room.winner_name}</span> : null}
                </div>
              </article>
            )
          })}
        </div>
      ) : (
        <p className="history-empty">还没有海龟汤对局记录。</p>
      )}
    </>
  )
}

export default function HistoryModal({ open, onClose, onLogin }) {
  const [data, setData] = useState(null)
  const [activeId, setActiveId] = useState('self')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    setLoading(true)
    setError('')
    api('/rooms/history')
      .then((result) => {
        setData(result)
        setActiveId('self')
      })
      .catch((err) => setError(err.message || '历史读取失败'))
      .finally(() => setLoading(false))
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null
  const needsLogin = error.includes('登录')
  return (
    <div className="toy-modal show history-modal" role="dialog" aria-modal="true" aria-labelledby="historyTitle" onClick={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <div className="modal-box">
        <h2 className="modal-title" id="historyTitle">历史</h2>
        <p className="modal-hint">海龟汤对局概况与已绑定小机记录</p>
        {loading ? <p className="history-empty">正在读取对局历史…</p> : null}
        {!loading && error ? (
          <div className="history-error">
            <p>{error}</p>
            {needsLogin ? <button type="button" className="pixel-btn" onClick={() => { onClose(); onLogin?.() }}>登录</button> : null}
          </div>
        ) : null}
        {!loading && !error ? <HistoryContent data={data} activeId={activeId} onActiveId={setActiveId} /> : null}
        <div className="modal-actions">
          <button type="button" className="pixel-btn secondary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
