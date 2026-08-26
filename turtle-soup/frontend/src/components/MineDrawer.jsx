import { useEffect } from 'react'
import { Link } from 'react-router-dom'

export default function MineDrawer({
  open,
  onClose,
  cedartoyMe,
  onLogin,
  onBind,
  onLogout,
  onUnbind,
  soupStats,
  onAccountAction,
}) {
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const user = cedartoyMe?.user
  const bindings = cedartoyMe?.bindings || []

  return (
    <div
      className="drawer-scrim mine-drawer-scrim show"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section className="drawer mine-drawer" aria-label="我的" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-head">
          <h2>我的</h2>
          <button type="button" className="pixel-btn secondary drawer-close" onClick={onClose} aria-label="关闭">×</button>
        </div>
        {!user ? (
          <>
            <p className="desc">当前未登录。</p>
            <p className="modal-hint">登录与注册已分开，避免误输用户名时创建空账号。</p>
            <button
              type="button"
              className="pixel-btn"
              onClick={() => {
                onClose()
                onLogin?.()
              }}
            >
              登录
            </button>
          </>
        ) : (
          <>
            <div className="mine-user-row">
              <p className="desc">用户：<span>{user.username}</span></p>
              <button type="button" className="unbind-btn" onClick={() => onAccountAction?.({ kind: 'rename', target: 'self', id: user.id, currentName: user.username })}>修改用户名</button>
            </div>
            <div className="mine-soup-stats" aria-label="海龟汤统计">
              <div><span>对局</span><b>{soupStats?.total_games || 0}</b></div>
              <div><span>答出</span><b>{soupStats?.win_count || 0}</b></div>
              <div><span>提问</span><b>{soupStats?.ask_count || 0}</b></div>
            </div>
            <ol className="rank-list">
              {bindings.length > 0 ? (
                bindings.map((binding) => (
                  <li key={binding.id}>
                    <span>{user.is_ai ? '人类' : 'AI'}</span>
                    <span>{binding.username}</span>
                    <span>
                      {!user.is_ai ? (
                        <>
                          <button type="button" className="unbind-btn" onClick={() => onAccountAction?.({ kind: 'rename', target: 'machine', id: binding.id, currentName: binding.username })}>
                            改名
                          </button>
                          <button type="button" className="unbind-btn" onClick={() => onAccountAction?.({ kind: 'password', target: 'machine', id: binding.id, currentName: binding.username })}>
                            重置密码
                          </button>
                          <button type="button" className="unbind-btn" onClick={() => onUnbind?.(binding.id)}>
                            解绑
                          </button>
                        </>
                      ) : '--'}
                    </span>
                  </li>
                ))
              ) : (
                <li><span>--</span><span>暂无绑定</span><span>--</span></li>
              )}
            </ol>
            {user.is_admin && (
              <Link className="pixel-btn secondary mine-admin-link" to="/admin">管理后台</Link>
            )}
            {!user.is_ai ? (
              <button
                type="button"
                className="pixel-btn"
                onClick={() => {
                  onClose()
                  onBind?.()
                }}
              >
                绑定
              </button>
            ) : null}
            <button
              type="button"
              className="pixel-btn secondary mine-logout-btn"
              onClick={() => {
                onClose()
                onLogout?.()
              }}
            >
              登出
            </button>
            <a className="mine-account-link" href="/">邮箱、密码、小机 Token 与注销设置请前往 CEDAR TOY「我的」</a>
          </>
        )}
      </section>
    </div>
  )
}
