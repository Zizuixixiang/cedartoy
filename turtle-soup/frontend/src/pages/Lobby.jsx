import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, getToken, post } from '../api'
import Leaderboard from '../components/Leaderboard.jsx'

export default function Lobby() {
  const nav = useNavigate()
  const [rooms, setRooms] = useState([])
  const [random, setRandom] = useState(null)
  const [custom, setCustom] = useState({ surface: '', answer: '' })
  const [generated, setGenerated] = useState(null)
  const [cooldown, setCooldown] = useState(0)
  const [error, setError] = useState('')
  const authed = getToken()

  const load = () => authed ? api('/rooms/').then(setRooms).catch(() => nav('/login')) : nav('/login')
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown(cooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])
  const roll = async () => setRandom(await api('/puzzles/random'))
  const create = async (body) => {
    try {
      const data = await post('/rooms/create', body)
      nav(`/room/${data.room_id}`)
    } catch (e) { setError(e.message) }
  }
  const generate = async () => {
    setCooldown(5)
    setGenerated(await post('/game/generate'))
  }
  return (
    <div className="grid-page">
      <section>
        <div className="section-head"><h2>大厅</h2><button onClick={load}>刷新</button></div>
        <div className="room-list">
          {rooms.map((room) => <Link className="room-item" to={`/room/${room.id}`} key={room.id}><b>{room.surface}</b><span>{room.status} · {room.ask_count} 问 · {room.active_players} 人</span></Link>)}
        </div>
      </section>
      <section className="create-area">
        <h2>创建房间</h2>
        <div className="panel">
          <h3>随机选题</h3>
          <p>{random?.surface || '从题库抽一题'}</p>
          <div className="actions"><button onClick={roll}>Roll</button><button className="primary" disabled={!random} onClick={() => create({ mode: 'random', puzzle_id: random.id })}>开始</button></div>
        </div>
        <div className="panel">
          <h3>自己填</h3>
          <textarea placeholder="汤面" value={custom.surface} onChange={(e) => setCustom({ ...custom, surface: e.target.value })} />
          <textarea placeholder="汤底" value={custom.answer} onChange={(e) => setCustom({ ...custom, answer: e.target.value })} />
          <button onClick={() => create({ mode: 'custom', ...custom })}>开始并投稿</button>
        </div>
        <div className="panel">
          <h3>AI 生成</h3>
          <p className="muted">AI 生成质量不稳定，仅供娱乐</p>
          {generated && <p>{generated.surface}</p>}
          <div className="actions"><button disabled={cooldown > 0} onClick={generate}>{cooldown ? `${cooldown}s` : '生成'}</button><button className="primary" disabled={!generated} onClick={() => create({ mode: 'generated', ...generated })}>满意，开始</button></div>
        </div>
        {error && <p className="error">{error}</p>}
      </section>
      <Leaderboard />
    </div>
  )
}
