import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, History, ListPlus, LogOut, Plus, Search, Shield, Trophy, UserRound } from 'lucide-react'
import { api, ensureGuestToken, logoutToGuest, post } from '../api'
import Leaderboard from '../components/Leaderboard.jsx'
import LoginModal from '../components/LoginModal.jsx'

const TITLE_MAX = 24
const TAG_FILTERS = ['红汤', '黑汤', '本格', '变格']

function roomTitle(room) {
  const title = (room.title || '').trim()
  if (title) return title.length > TITLE_MAX ? `${title.slice(0, TITLE_MAX)}…` : title
  const text = (room.surface || '未命名汤面').trim()
  const fallback = text.split(/[，。！？,.!?]/)[0]
  return fallback.length > TITLE_MAX ? `${fallback.slice(0, TITLE_MAX)}…` : fallback
}

function parseTags(tags) {
  if (!tags) return []
  if (Array.isArray(tags)) return tags.filter(Boolean)
  return String(tags).split(/[,，、\s]+/).map((tag) => tag.trim()).filter(Boolean)
}

function roomTitleText(room) {
  const title = (room.title || '').trim()
  if (title) return title
  const text = (room.surface || '未命名汤面').trim()
  return text.split(/[，。！？,.!?]/)[0] || '未命名汤面'
}

function matchesRoomFilters(room, query, activeTags) {
  const q = query.trim().toLowerCase()
  if (q && !roomTitleText(room).toLowerCase().includes(q)) return false
  if (activeTags.length > 0) {
    const roomTags = parseTags(room.tags)
    if (!activeTags.some((tag) => roomTags.includes(tag))) return false
  }
  return true
}

function initials(player) {
  const name = player?.username || player?.player?.username || 'A'
  return name.slice(0, 1).toUpperCase()
}

export default function Lobby() {
  const nav = useNavigate()
  const [rooms, setRooms] = useState([])
  const [random, setRandom] = useState(null)
  const [custom, setCustom] = useState({ surface: '', answer: '' })
  const [generated, setGenerated] = useState(null)
  const [aiCooldown, setAiCooldown] = useState(0)
  const [randomCooldown, setRandomCooldown] = useState(0)
  const [cooldownSeconds, setCooldownSeconds] = useState(5)
  const [error, setError] = useState('')
  const [me, setMe] = useState(null)
  const [createTab, setCreateTab] = useState('random')
  const [bottomTab, setBottomTab] = useState('rooms')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [roomSearch, setRoomSearch] = useState('')
  const [activeTagFilters, setActiveTagFilters] = useState([])
  const [loginOpen, setLoginOpen] = useState(false)

  const load = async () => {
    try {
      await ensureGuestToken()
      const [roomRows, profile] = await Promise.all([
        api('/rooms/'),
        api('/auth/me').catch(() => null),
      ])
      setRooms(roomRows)
      setMe(profile?.player || null)
      api('/game/public-settings').then((data) => setCooldownSeconds(Number(data.generate_cooldown_seconds) || 5)).catch(() => {})
    } catch (e) {
      setError(e.message)
    }
  }
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (aiCooldown <= 0) return
    const t = setTimeout(() => setAiCooldown(aiCooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [aiCooldown])
  useEffect(() => {
    if (randomCooldown <= 0) return
    const t = setTimeout(() => setRandomCooldown(randomCooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [randomCooldown])
  const roll = async () => {
    setRandomCooldown(cooldownSeconds)
    setRandom(await api('/puzzles/random'))
  }
  const create = async (body) => {
    try {
      const data = await post('/rooms/create', body)
      nav(`/room/${data.room_id}`)
    } catch (e) { setError(e.message) }
  }
  const generate = async () => {
    setAiCooldown(cooldownSeconds)
    setGenerated(await post('/game/generate'))
  }
  const logout = async () => {
    await logoutToGuest()
    await load()
  }
  const activeRooms = rooms.filter((room) => room.status === 'waiting' || room.status === 'playing')
  const online = activeRooms.reduce((sum, room) => sum + Number(room.active_players || 0), 0)
  const displayRooms = [...rooms].sort((a, b) => {
    const players = Number(b.active_players || 0) - Number(a.active_players || 0)
    if (players) return players
    return Number(b.ask_count || 0) - Number(a.ask_count || 0)
  })
  const filteredRooms = displayRooms.filter((room) => matchesRoomFilters(room, roomSearch, activeTagFilters))
  const toggleTagFilter = (tag) => {
    setActiveTagFilters((prev) => (
      prev.includes(tag) ? prev.filter((item) => item !== tag) : [...prev, tag]
    ))
  }
  const createPanel = (
    <section className="pixel-create">
      <div className="terminal-head">
        <span className="lights"><i /><i /><i /></span>
        <strong>创建房间</strong>
        <small>创建会话</small>
      </div>
      <div className="create-tabs" role="tablist" aria-label="创建房间方式">
        {[
          ['random', '随机'],
          ['custom', '自填'],
          ['ai', 'AI 生成'],
        ].map(([id, label]) => (
          <button type="button" className={createTab === id ? 'active' : ''} key={id} onClick={() => setCreateTab(id)}>{label}</button>
        ))}
      </div>
      {createTab === 'random' && (
        <div className="create-body">
          <label className="terminal-label">选题
            <select value={random?.id || ''} onChange={roll}>
              <option value={random?.id || ''}>{random ? `#${random.id} ${random.title || '随机题'}` : '经典推理题库'}</option>
            </select>
          </label>
          <div className="terminal-preview" aria-live="polite">
            <p>&gt; 正在等待选题...</p>
            <p>&gt; 当前题目：<b>{random?.title || '尚未抽取'}</b></p>
            <p className="type-line">&gt; {random?.surface || '点击“随机抽题”抽取一碗未解之汤。'}</p>
          </div>
          <div className="actions">
            <button type="button" disabled={randomCooldown > 0} onClick={roll}>{randomCooldown ? `${randomCooldown}s` : '随机抽题'}</button>
            <button type="button" className="pixel-primary" disabled={!random} onClick={() => create({ mode: 'random', puzzle_id: random.id })}>创建</button>
          </div>
        </div>
      )}
      {createTab === 'custom' && (
        <div className="create-body">
          <label className="terminal-label">汤面<textarea value={custom.surface} onChange={(e) => setCustom({ ...custom, surface: e.target.value })} /></label>
          <label className="terminal-label">汤底<textarea value={custom.answer} onChange={(e) => setCustom({ ...custom, answer: e.target.value })} /></label>
          <button type="button" className="pixel-primary wide" onClick={() => create({ mode: 'custom', ...custom })}>创建</button>
        </div>
      )}
      {createTab === 'ai' && (
        <div className="create-body">
          <div className="terminal-preview">
            <p>&gt; 生成器已就绪</p>
            <p>{generated?.surface || '生成提示词可在管理界面的运行参数里调整。'}</p>
          </div>
          <div className="actions">
            <button type="button" disabled={aiCooldown > 0} onClick={generate}>{aiCooldown ? `${aiCooldown}s` : '生成'}</button>
            <button type="button" className="pixel-primary" disabled={!generated} onClick={() => create({ mode: 'generated', ...generated })}>创建</button>
          </div>
        </div>
      )}
      {error && <p className="error terminal-error">{error}</p>}
    </section>
  )
  return (
    <div className="lobby-page">
      <header className="lobby-topbar">
        <a className="lobby-back" href="/" aria-label="返回首页"><ArrowLeft size={22} /></a>
        <div className="lobby-title"><span className="pixel-mark">▣</span><span>游戏大厅</span></div>
        <div className="lobby-status"><span className="online-dot" /> 在线：<b>{String(Math.max(online, activeRooms.length)).padStart(2, '0')}</b><span>房间：<b>{String(activeRooms.length).padStart(2, '0')}</b></span></div>
        <nav className="lobby-actions">
          {me?.is_admin && <Link to="/add-puzzle"><ListPlus size={17} />加题</Link>}
          {me?.is_admin && <Link to="/admin"><Shield size={17} />管理</Link>}
          {!me || me?.is_guest ? (
            <button type="button" className="avatar-pill" aria-label="登录" onClick={() => setLoginOpen(true)}>{initials(me)}</button>
          ) : (
            <Link className="avatar-pill" to="/profile" aria-label="个人资料">{initials(me)}</Link>
          )}
          {!me?.is_guest && <button type="button" onClick={logout}><LogOut size={17} />退出</button>}
        </nav>
      </header>

      <main className={`lobby-main ${bottomTab !== 'rooms' ? 'single-view' : ''}`}>
        {bottomTab === 'leaderboard' && (
          <section className="lobby-view">
            <div className="rooms-head">
              <h1><span>▥</span>排行榜</h1>
              <button type="button" onClick={() => setBottomTab('rooms')}>返回房间列表</button>
            </div>
            <Leaderboard />
          </section>
        )}
        {bottomTab === 'history' && (
          <section className="lobby-view">
            <div className="rooms-head">
              <h1><span>◴</span>历史</h1>
              <button type="button" onClick={() => setBottomTab('rooms')}>返回房间列表</button>
            </div>
            <div className="panel pixel-view-panel">历史记录在个人页汇总展示。</div>
          </section>
        )}
        {bottomTab === 'mine' && (
          <section className="lobby-view">
            <div className="rooms-head">
              <h1><span>☻</span>我的</h1>
              <button type="button" onClick={() => setBottomTab('rooms')}>返回房间列表</button>
            </div>
            <div className="panel pixel-view-panel">
              <p>当前用户：{me?.username || `游客${me?.id || ''}`}</p>
              <Link className="enter-room profile-enter" to="/profile">进入个人页 →</Link>
            </div>
          </section>
        )}
        {bottomTab === 'rooms' && <section className="rooms-area">
          <div className="rooms-head">
            <h1><span>☷</span>活跃房间</h1>
            <button type="button" onClick={load}>按热度排序</button>
          </div>
          <div className="rooms-toolbar">
            <label className="room-search">
              <Search size={18} aria-hidden="true" />
              <input
                type="search"
                value={roomSearch}
                onChange={(e) => setRoomSearch(e.target.value)}
                placeholder="搜索汤名…"
                aria-label="按汤名搜索房间"
              />
            </label>
            <div className="room-tag-filters" role="group" aria-label="按标签筛选">
              {TAG_FILTERS.map((tag) => (
                <button
                  type="button"
                  key={tag}
                  className={`room-tag-filter${activeTagFilters.includes(tag) ? ' active' : ''}`}
                  aria-pressed={activeTagFilters.includes(tag)}
                  onClick={() => toggleTagFilter(tag)}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
          <div className="pixel-room-list">
            {filteredRooms.map((room) => {
              const tags = parseTags(room.tags)
              return (
                <article className="pixel-room-card" key={room.id}>
                  <div className="room-code">房间 #{room.id}</div>
                  <div className="room-glyph" aria-hidden="true">?</div>
                  <div className="room-copy">
                    <h2>{roomTitle(room)}</h2>
                    <p>{room.surface}</p>
                    <div className="room-meta">
                      {tags.map((tag) => <span className="soup-badge" key={tag}>{tag}</span>)}
                      <span className="soup-badge pale">{room.status === 'finished' ? '已结束' : '进行中'}</span>
                    </div>
                    <div className="room-stats"><span>提问 {room.ask_count || 0}</span><span>在房 {room.active_players || 0}</span></div>
                  </div>
                  <Link className="enter-room" to={`/room/${room.id}`}>进入 →</Link>
                </article>
              )
            })}
            {rooms.length === 0 && <div className="empty-state pixel-empty">暂无房间，右侧终端可以创建新房间。</div>}
            {rooms.length > 0 && filteredRooms.length === 0 && (
              <div className="empty-state pixel-empty">没有匹配的房间，试试换个关键词或标签。</div>
            )}
          </div>
        </section>}
        <aside className="create-dock">{createPanel}</aside>
      </main>

      <nav className="lobby-bottom-nav" aria-label="底部导航">
        {[
          ['leaderboard', <Trophy size={22} />, '排行榜'],
          ['history', <History size={22} />, '历史'],
          ['mine', <UserRound size={22} />, '我的'],
        ].map(([id, icon, label]) => (
          <button type="button" className={bottomTab === id ? 'active' : ''} key={id} onClick={() => {
            setBottomTab(id)
          }}><span className="bottom-icon">{icon}</span><span>{label}</span></button>
        ))}
      </nav>
      <button className="create-fab" type="button" onClick={() => setDrawerOpen(true)} aria-label="创建房间"><Plus size={28} /></button>
      <div className={`create-drawer ${drawerOpen ? 'show' : ''}`} onClick={(event) => {
        if (event.target === event.currentTarget) setDrawerOpen(false)
      }}>
        <div className="create-drawer-panel">
          <div className="drawer-grip" />
          {createPanel}
        </div>
      </div>
      <LoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        onSuccess={(player) => {
          setMe(player)
          load()
        }}
      />
    </div>
  )
}
