import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { LogOut, Shield, Soup, UserRound } from 'lucide-react'
import { api, ensureGuestToken, logoutToGuest } from './api'

export default function App() {
  const location = useLocation()
  const isLobby = location.pathname === '/'
  const [me, setMe] = useState(null)
  useEffect(() => {
    ensureGuestToken()
      .then(() => api('/auth/me'))
      .then((data) => setMe(data?.player || null))
      .catch(() => setMe(null))
  }, [location.pathname])
  const logout = () => { logoutToGuest().then(() => setMe(null)) }
  return (
    <div className="app-shell">
      {!isLobby && (
        <header className="topbar">
          <Link className="brand" to="/"><Soup size={24} /> 海龟汤</Link>
          <nav>
            <Link to="/profile"><UserRound size={18} /> 个人</Link>
            {me?.is_admin && <Link to="/add-puzzle">加题</Link>}
            {me?.is_admin && <Link to="/admin"><Shield size={18} /> 管理</Link>}
            {!me?.is_guest && <button className="icon-text" onClick={logout}><LogOut size={18} /> 退出</button>}
          </nav>
        </header>
      )}
      <main className={isLobby ? 'lobby-shell-main' : ''}><Outlet /></main>
    </div>
  )
}
