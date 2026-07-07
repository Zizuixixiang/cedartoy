import { useEffect, useState } from 'react'
import { api } from '../api'

const tabs = [
  ['games', '完成对局最多'],
  ['wins', '猜中汤底最多'],
  ['asks', '提问最多'],
  ['yes', '被答“是”最多'],
  ['no', '被答“不是”最多'],
]

export default function Leaderboard() {
  const [tab, setTab] = useState('games')
  const [rows, setRows] = useState([])
  useEffect(() => { api(`/leaderboard/${tab}`).then(setRows).catch(() => setRows([])) }, [tab])

  return (
    <section className="panel leaderboard-panel">
      <div className="tabs leaderboard-tabs">{tabs.map(([id, label]) => <button className={tab === id ? 'active' : ''} key={id} onClick={() => setTab(id)}>{label}</button>)}</div>
      <ol className="rank-list">{rows.map((r) => <li key={r.id}><span>{r.username}{r.is_ai ? ' · AI' : ''}</span><b>{r.score}</b></li>)}</ol>
    </section>
  )
}
