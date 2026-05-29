import { useEffect, useState } from 'react'
import { api, post, put, del } from '../api'

const tabs = ['overview', 'submissions', 'players', 'rooms', 'bans', 'reports', 'flags', 'api-configs', 'settings']

export default function Admin() {
  const [tab, setTab] = useState('overview')
  const [rows, setRows] = useState(null)
  const [settingsDraft, setSettingsDraft] = useState({})
  const [error, setError] = useState('')
  const load = () => api(`/admin/${tab}`).then((data) => { setRows(data); setError('') }).catch((e) => setError(e.message))
  useEffect(() => { load() }, [tab])
  useEffect(() => {
    if (tab !== 'settings' || !Array.isArray(rows)) return
    setSettingsDraft(Object.fromEntries(rows.map((row) => [row.key, row.value])))
  }, [tab, rows])
  const addBan = async () => { const ip = prompt('IP'); if (ip) { await post('/admin/bans', { ip }); load() } }
  const addApi = async () => {
    const name = prompt('名称'); const api_url = prompt('Base URL'); const api_key = prompt('API Key'); const model = prompt('模型')
    if (name && api_url && api_key && model) { await post('/admin/api-configs', { name, api_url, api_key, model }); load() }
  }
  const saveSetting = async (key) => {
    await put(`/admin/settings/${key}`, { value: settingsDraft[key] ?? '' })
    load()
  }
  return (
    <section className="admin-page">
      <h2>管理</h2>
      <div className="tabs">{tabs.map((t) => <button className={tab === t ? 'active' : ''} key={t} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === 'bans' && <button onClick={addBan}>新增封禁</button>}
      {tab === 'api-configs' && <button onClick={addApi}>新增裁判 API</button>}
      {error && <p className="error">{error}</p>}
      {tab === 'settings' ? (
        <div className="settings-form">
          {Array.isArray(rows) && rows.map((r) => (
            <div className="setting-row" key={r.key}>
              <label htmlFor={`setting-${r.key}`}>{r.key}</label>
              <input
                id={`setting-${r.key}`}
                value={settingsDraft[r.key] ?? ''}
                onChange={(e) => setSettingsDraft((draft) => ({ ...draft, [r.key]: e.target.value }))}
              />
              <button className="primary" onClick={() => saveSetting(r.key)}>保存</button>
            </div>
          ))}
        </div>
      ) : (
        <div className="admin-table">
          {Array.isArray(rows) ? rows.map((r) => <div className="admin-row" key={`${tab}-${r.id || r.key}`}>
            <pre>{JSON.stringify(r, null, 2)}</pre>
            {tab === 'submissions' && <><button onClick={() => post(`/admin/submissions/${r.id}/add`, r).then(load)}>收录</button><button onClick={() => post(`/admin/submissions/${r.id}/ignore`).then(load)}>忽略</button></>}
            {tab === 'bans' && <button onClick={() => del(`/admin/bans/${r.id}`).then(load)}>解封</button>}
          </div>) : <pre>{JSON.stringify(rows, null, 2)}</pre>}
        </div>
      )}
    </section>
  )
}
