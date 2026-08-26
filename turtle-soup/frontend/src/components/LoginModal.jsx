import { useEffect, useRef, useState } from 'react'
import { loginAccount, validateLoginInput } from '../api'
import PasswordInput from './PasswordInput.jsx'

export default function LoginModal({ open, onClose, onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState('login')
  const backdropPointerId = useRef(null)
  const usernameRef = useRef(null)
  const passwordRef = useRef(null)
  const confirmPasswordRef = useRef(null)

  useEffect(() => {
    setConfirmPassword('')
    if (open) {
      setMode('login')
      setError('')
    }
  }, [open])

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
    const resolvedUsername = (usernameRef.current?.value ?? username).trim()
    const resolvedPassword = passwordRef.current?.value ?? password
    const validationError = validateLoginInput(resolvedUsername, resolvedPassword)
    if (validationError) {
      setError(validationError)
      return
    }
    if (mode === 'register') {
      const resolvedConfirmPassword = confirmPasswordRef.current?.value ?? confirmPassword
      if (!resolvedConfirmPassword) {
        setError('请再次输入密码')
        return
      }
      if (resolvedPassword !== resolvedConfirmPassword) {
        setError('两次输入的密码不一致')
        return
      }
    }
    setLoading(true)
    try {
      const player = await loginAccount(resolvedUsername, resolvedPassword, mode)
      setUsername('')
      setPassword('')
      setConfirmPassword('')
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
        <h2 className="modal-title" id="loginTitle">{mode === 'register' ? '注册人类账号' : '账号登录'}</h2>
        <div className="auth-mode-tabs" aria-label="选择登录或注册">
          <button type="button" className={`pixel-btn${mode === 'login' ? '' : ' secondary'}`} onClick={() => { setMode('login'); setConfirmPassword(''); setError('') }}>登录</button>
          <button type="button" className={`pixel-btn${mode === 'register' ? '' : ' secondary'}`} onClick={() => { setMode('register'); setConfirmPassword(''); setError('') }}>注册</button>
        </div>
        <p className="modal-hint">
          {mode === 'register'
            ? '创建新的人类账号；已有用户名不会被登录或覆盖。'
            : '登录已有的人类账号；用户名不存在时不会自动注册。'}
        </p>
        <label className="field">
          <span className="field-label">用户名</span>
          <input
            ref={usernameRef}
            type="text"
            maxLength={20}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <span className="field-hint">2-20 个字符，仅支持字母、数字、下划线、中文</span>
        </label>
        <div className="field">
          <label className="field-label" htmlFor="soupLoginPassword">密码</label>
          <PasswordInput
            key={mode}
            id="soupLoginPassword"
            ref={passwordRef}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
          />
          <span className="field-hint">至少 6 位</span>
        </div>
        {mode === 'register' ? (
          <div className="field" id="soupLoginConfirmField">
            <label className="field-label" htmlFor="soupLoginConfirmPassword">确认密码</label>
            <PasswordInput
              id="soupLoginConfirmPassword"
              ref={confirmPasswordRef}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit()
              }}
            />
            <span className="field-hint">请再次输入相同密码</span>
          </div>
        ) : null}
        <div className="modal-msg">{error}</div>
        <div className="modal-actions">
          <button type="button" className="pixel-btn secondary" disabled={loading} onClick={onClose}>取消</button>
          <button type="button" className="pixel-btn" id="loginSubmit" disabled={loading} onClick={submit}>
            {loading ? '…' : (mode === 'register' ? '注册' : '登录')}
          </button>
        </div>
      </div>
    </div>
  )
}
