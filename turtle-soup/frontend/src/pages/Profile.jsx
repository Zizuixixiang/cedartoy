import { useEffect, useState } from 'react'
import { api } from '../api'
import { HistoryContent } from '../components/HistoryModal.jsx'

export default function Profile() {
  const [data, setData] = useState(null)
  const [activeId, setActiveId] = useState('self')
  const [error, setError] = useState('')
  useEffect(() => {
    api('/rooms/history').then(setData).catch((err) => setError(err.message || '请稍后再试'))
  }, [])
  if (error) return <div className="empty-state"><p>历史加载失败：{error}</p></div>
  if (!data) return <div className="loading">加载中</div>
  return (
    <section className="profile-page">
      <h2>历史</h2>
      <p className="muted">海龟汤对局概况与已绑定小机记录</p>
      <div className="panel history-page-panel">
        <HistoryContent data={data} activeId={activeId} onActiveId={setActiveId} />
      </div>
    </section>
  )
}
