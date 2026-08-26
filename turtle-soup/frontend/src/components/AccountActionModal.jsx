import { useEffect, useState } from 'react'
import PasswordInput from './PasswordInput.jsx'

const USERNAME_RE = /^[a-zA-Z0-9_\u4e00-\u9fff]{2,20}$/

export default function AccountActionModal({ action, onClose, onSubmit }) {
  const [value, setValue] = useState('')
  const [confirmValue, setConfirmValue] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setValue(action?.kind === 'rename' ? action.currentName || '' : '')
    setConfirmValue('')
    setError('')
  }, [action])

  if (!action) return null
  const renaming = action.kind === 'rename'

  const submit = async () => {
    const next = value.trim()
    setError('')
    if (renaming) {
      if (!USERNAME_RE.test(next)) {
        setError('用户名须为 2-20 个字符，且只能包含字母、数字、下划线和中文')
        return
      }
      if (next === action.currentName) {
        setError('新用户名与当前名称相同')
        return
      }
    } else {
      if (value.length < 6) {
        setError('新密码至少 6 位')
        return
      }
      if (value !== confirmValue) {
        setError('两次输入的新密码不一致')
        return
      }
    }
    setLoading(true)
    try {
      await onSubmit(action, renaming ? next : value)
      onClose()
    } catch (err) {
      setError(err.message || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="toy-modal show account-action-modal" role="dialog" aria-modal="true" aria-labelledby="accountActionTitle" onClick={(event) => {
      if (event.target === event.currentTarget && !loading) onClose()
    }}>
      <div className="modal-box">
        <h2 className="modal-title" id="accountActionTitle">{renaming ? '修改用户名' : '重置小机密码'}</h2>
        <p className="modal-hint">
          {renaming
            ? `当前名称：「${action.currentName}」。改名后请用新用户名登录，现有登录不会失效。`
            : `为小机「${action.currentName}」设置新密码。`}
        </p>
        <div className="field">
          <label className="field-label" htmlFor="accountActionValue">{renaming ? '新用户名' : '新密码'}</label>
          {renaming ? (
            <input
              id="accountActionValue"
              type="text"
              maxLength={20}
              autoComplete="username"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          ) : (
            <PasswordInput
              id="accountActionValue"
              autoComplete="new-password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          )}
        </div>
        {!renaming ? (
          <div className="field">
            <label className="field-label" htmlFor="accountActionConfirmValue">确认新密码</label>
            <PasswordInput
              id="accountActionConfirmValue"
              autoComplete="new-password"
              value={confirmValue}
              onChange={(event) => setConfirmValue(event.target.value)}
            />
          </div>
        ) : null}
        <div className="modal-msg">{error}</div>
        <div className="modal-actions">
          <button type="button" className="pixel-btn secondary" disabled={loading} onClick={onClose}>取消</button>
          <button type="button" className="pixel-btn" disabled={loading} onClick={submit}>{loading ? '…' : '确认'}</button>
        </div>
      </div>
    </div>
  )
}
