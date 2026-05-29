import { useEffect, useState } from 'react'
import { api } from '../api'

const tabs = [
  ['games', '对局最多'],
  ['wins', '答对最多'],
  ['asks', '提问最多'],
  ['yes', 'yes 最多'],
  ['no', 'no 最多'],
]

export default function Leaderboard() {
  const [tab, setTab] = useState('games')
  const [rows, setRows] = useState([])
  useEffect(() => { api(`/leaderboard/${tab}`).then(setRows).catch(() => setRows([])) }, [tab])
  return (
    <section className="panel">
      <div className="tabs">{tabs.map(([id, label]) => <button className={tab === id ? 'active' : ''} key={id} onClick={() => setTab(id)}>{label}</button>)}</div>
      <ol className="rank-list">{rows.map((r) => <li key={r.id}><span>{r.username}{r.is_ai ? ' · AI' : ''}</span><b>{r.score}</b></li>)}</ol>
    </section>
  )
}
