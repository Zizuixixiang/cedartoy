import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { post, setToken } from '../api'

export default function Login() {
  const nav = useNavigate()
  const [form, setForm] = useState({ username: '', password: '' })
  const [error, setError] = useState('')
  const enter = async (guest = false) => {
    try {
      const data = guest ? await post('/auth/guest') : await post('/auth/register', { ...form, source: 'web' })
      setToken(data.token)
      nav('/')
    } catch (e) { setError(e.message) }
  }
  return (
    <section className="auth-page">
      <div className="auth-box">
        <h1>海龟汤</h1>
        <button className="primary wide" onClick={() => enter(true)}>直接进入</button>
        <div className="divider">或使用账号</div>
        <input placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input placeholder="密码" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <button className="wide" onClick={() => enter(false)}>注册 / 登录</button>
        {error && <p className="error">{error}</p>}
      </div>
    </section>
  )
}
