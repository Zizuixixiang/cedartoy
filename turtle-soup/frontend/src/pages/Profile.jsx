import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Profile() {
  const [data, setData] = useState(null)
  useEffect(() => { api('/rooms/profile/me').then(setData) }, [])
  if (!data) return <div className="loading">加载中</div>
  const p = data.player
  return (
    <section className="profile-page">
      <h2>{p.username}</h2>
      <div className="stats">
        <div>对局 <b>{p.game_count}</b></div><div>答对 <b>{p.win_count}</b></div><div>提问 <b>{p.ask_count}</b></div>
        <div>是 <b>{p.ask_count_y}</b></div><div>否 <b>{p.ask_count_n}</b></div><div>不相关 <b>{p.ask_count_u}</b></div>
      </div>
      <h3>历史对局</h3>
      <div className="room-list">{data.rooms.map((r) => <div className="room-item" key={r.id}><b>{r.surface}</b><span>{r.status} · {r.created_at}</span></div>)}</div>
    </section>
  )
}
