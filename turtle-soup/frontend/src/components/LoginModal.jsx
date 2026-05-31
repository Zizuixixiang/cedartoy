import { useEffect, useRef, useState } from 'react'
import { loginOrRegister, validateLoginInput } from '../api'

export default function LoginModal({ open, onClose, onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const backdropPointerId = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const submit = async () => {
    setError('')
    const validationError = validateLoginInput(username.trim(), password)
    if (validationError) {
      setError(validationError)
      return
    }
    setLoading(true)
    try {
      const player = await loginOrRegister(username.trim(), password)
      setUsername('')
      setPassword('')
      onSuccess?.(player)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="toy-modal show"
      id="loginModal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="loginTitle"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) {
          backdropPointerId.current = event.pointerId
        }
      }}
      onPointerUp={(event) => {
        if (
          event.target === event.currentTarget
          && backdropPointerId.current === event.pointerId
        ) {
          onClose()
        }
        backdropPointerId.current = null
      }}
      onPointerCancel={() => {
        backdropPointerId.current = null
      }}
    >
      <div className="modal-box">
        <h2 className="modal-title" id="loginTitle">登录 / 注册</h2>
        <p className="modal-hint">没有账号？输入用户名和密码直接注册</p>
        <label className="field">
          <span className="field-label">用户名</span>
          <input
            type="text"
            maxLength={20}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <span className="field-hint">2-20 个字符，仅支持字母、数字、下划线、中文</span>
        </label>
        <label className="field">
          <span className="field-label">密码</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
          />
          <span className="field-hint">至少 6 位</span>
        </label>
        <div className="modal-msg">{error}</div>
        <div className="modal-actions">
          <button type="button" className="pixel-btn secondary" disabled={loading} onClick={onClose}>取消</button>
          <button type="button" className="pixel-btn" id="loginSubmit" disabled={loading} onClick={submit}>
            {loading ? '…' : '确定'}
          </button>
        </div>
      </div>
    </div>
  )
}
