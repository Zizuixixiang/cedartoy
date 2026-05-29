import { Link, Outlet, useNavigate } from 'react-router-dom'
import { LogOut, Shield, Soup, UserRound } from 'lucide-react'
import { clearToken, getToken } from './api'

export default function App() {
  const navigate = useNavigate()
  const logout = () => {
    clearToken()
    navigate('/login')
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/"><Soup size={24} /> 海龟汤</Link>
        <nav>
          <Link to="/profile"><UserRound size={18} /> 个人</Link>
          <Link to="/admin"><Shield size={18} /> 管理</Link>
          {getToken() && <button className="icon-text" onClick={logout}><LogOut size={18} /> 退出</button>}
        </nav>
      </header>
      <main><Outlet /></main>
    </div>
  )
}
