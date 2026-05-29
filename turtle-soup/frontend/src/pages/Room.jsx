import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getToken, api, post } from '../api'
import GameLog from '../components/GameLog.jsx'
import HintModal from '../components/HintModal.jsx'
import NoteBoard from '../components/NoteBoard.jsx'

export default function Room() {
  const { roomId } = useParams()
  const [room, setRoom] = useState(null)
  const [logs, setLogs] = useState([])
  const [notes, setNotes] = useState([])
  const [content, setContent] = useState('')
  const [hint, setHint] = useState(null)
  const [answer, setAnswer] = useState('')
  const logRef = useRef(null)
  const load = async () => {
    const data = await api(`/rooms/${roomId}`)
    setRoom(data); setLogs(data.logs || []); setNotes(data.notes || [])
  }
  useEffect(() => { load() }, [roomId])
  useEffect(() => {
    const es = new EventSource(`/soup/api/sse/${roomId}?token=${encodeURIComponent(getToken())}`)
    es.addEventListener('new_log', (e) => setLogs((items) => [...items, JSON.parse(e.data)]))
    es.addEventListener('hint_offer', (e) => setHint(JSON.parse(e.data)))
    es.addEventListener('hint_resolved', (e) => { const d = JSON.parse(e.data); setHint(null); if (d.hint_text) setLogs((items) => [...items, { id: `hint-${d.log_id}`, type: 'system', content: `提示：${d.hint_text}` }]) })
    es.addEventListener('game_over', (e) => { const d = JSON.parse(e.data); setAnswer(d.answer); setRoom((r) => ({ ...r, status: 'finished' })) })
    es.addEventListener('new_note', (e) => setNotes((items) => [JSON.parse(e.data), ...items]))
    es.addEventListener('update_note', (e) => { const d = JSON.parse(e.data); setNotes((items) => items.map((n) => n.id === d.id ? d : n)) })
    es.addEventListener('delete_note', (e) => { const d = JSON.parse(e.data); setNotes((items) => items.filter((n) => n.id !== d.id)) })
    return () => es.close()
  }, [roomId])
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }, [logs])
  const send = async (kind) => {
    if (!content.trim()) return
    await post(`/game/${kind}`, { room_id: roomId, content })
    setContent('')
  }
  const report = async (log) => {
    const reason = prompt('举报原因')
    if (reason) await post('/report', { room_id: roomId, log_id: log.id, target_player_id: log.player_id, reason })
  }
  if (!room) return <div className="loading">加载中</div>
  return (
    <div className="room-page">
      <section className="game-main">
        <h1>{room.surface}</h1>
        {answer && <div className="answer">汤底：{answer}</div>}
        <div className="log-wrap" ref={logRef}><GameLog logs={logs} onReport={report} /></div>
        <div className="input-bar">
          <input maxLength="200" value={content} onChange={(e) => setContent(e.target.value)} placeholder="输入问题或猜测" />
          <button onClick={() => send('ask')}>提问</button>
          <button className="primary" onClick={() => send('guess')}>猜汤底</button>
        </div>
      </section>
      <NoteBoard roomId={roomId} notes={notes} setNotes={setNotes} />
      <HintModal hint={hint} onReject={() => post('/game/hint/respond', { room_id: roomId, log_id: hint.log_id, accept: false })} onAccept={() => post('/game/hint/respond', { room_id: roomId, log_id: hint.log_id, accept: true })} />
    </div>
  )
}
