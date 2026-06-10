import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, History, House, ListPlus, LogOut, Plus, RefreshCw, Search, Shield, Trophy, UserRound } from 'lucide-react'
import { api, ensureGuestToken, logoutToGuest, post } from '../api'
import BindModal from '../components/BindModal.jsx'
import ColoredSurface from '../components/ColoredSurface.jsx'
import Leaderboard from '../components/Leaderboard.jsx'
import LoginModal from '../components/LoginModal.jsx'
import MineDrawer from '../components/MineDrawer.jsx'
import PlaceholderModal from '../components/PlaceholderModal.jsx'
import { stripSurfaceColorMarkers } from '../utils/display.js'

const TITLE_MAX = 24
const TAG_FILTERS = ['红汤', '黑汤', '本格', '变格']
const AI_STYLE_OPTIONS = [
  ['', '不指定风格'],
  ['cozy', '日常温馨风'],
  ['absurd', '荒诞喜剧风'],
  ['mystery', '本格推理风'],
  ['fantasy', '奇幻异世界风'],
  ['history', '历史民俗风'],
  ['scifi', '科幻未来风'],
  ['horror', '黑暗惊悚风'],
]

function roomTitle(room) {
  const title = (room.title || '').trim()
  if (title) return title.length > TITLE_MAX ? `${title.slice(0, TITLE_MAX)}…` : title
  const text = stripSurfaceColorMarkers(room.surface || '未命名汤面').trim()
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
  const text = stripSurfaceColorMarkers(room.surface || '未命名汤面').trim()
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
  const location = useLocation()
  const [rooms, setRooms] = useState([])
  const [puzzles, setPuzzles] = useState([])
  const [random, setRandom] = useState(null)
  const [selectedPuzzleId, setSelectedPuzzleId] = useState('')
  const [custom, setCustom] = useState({ surface: '', answer: '' })
  const [generated, setGenerated] = useState(null)
  const [aiStyle, setAiStyle] = useState('')
  const [aiGenerating, setAiGenerating] = useState(false)
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
  const [creating, setCreating] = useState(false)
  const [mineOpen, setMineOpen] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [cedartoyMe, setCedartoyMe] = useState(null)
  const [spoilerConfirm, setSpoilerConfirm] = useState(null)

  const load = async () => {
    try {
      await ensureGuestToken()
      const [roomRows, profile] = await Promise.all([
        api('/rooms/'),
        api('/auth/me').catch(() => null),
      ])
      setRooms(roomRows)
      setMe(profile?.player || null)
      api('/puzzles/public').then((rows) => setPuzzles(rows)).catch(() => setPuzzles([]))
      api('/game/public-settings').then((data) => setCooldownSeconds(Number(data.generate_cooldown_seconds) || 5)).catch(() => {})
    } catch (e) {
      setError(e.message)
    }
  }
  useEffect(() => { load() }, [])

  const loadCedartoyMe = async () => {
    const token = localStorage.getItem('cedartoy_token') || ''
    if (!token) { setCedartoyMe(null); return }
    try {
      const res = await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) throw new Error()
      setCedartoyMe(await res.json())
    } catch { setCedartoyMe(null) }
  }

  const unbindAi = async (aiUserId) => {
    if (!confirm('确定解绑该 AI 账号？')) return
    const token = localStorage.getItem('cedartoy_token') || ''
    try {
      const res = await fetch('/api/auth/bind', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ai_user_id: Number(aiUserId) }),
      })
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || '解绑失败') }
      await loadCedartoyMe()
    } catch (e) { alert(e.message) }
  }

  const openMine = () => {
    setMineOpen(true)
    loadCedartoyMe()
  }
  useEffect(() => {
    const tab = location.state?.tab
    if (tab === 'leaderboard' || tab === 'mine') {
      setBottomTab(tab)
    }
    if (tab === 'history') {
      setHistoryOpen(true)
    }
  }, [location.state?.tab])
  useEffect(() => {
    if (randomCooldown <= 0) return
    const t = setTimeout(() => setRandomCooldown(randomCooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [randomCooldown])
  const roll = async () => {
    setRandomCooldown(cooldownSeconds)
    const puzzle = await api('/puzzles/random')
    setRandom(puzzle)
    setSelectedPuzzleId(String(puzzle.id))
  }
  const selectPuzzle = (value) => {
    setSelectedPuzzleId(value)
    setRandom(puzzles.find((puzzle) => String(puzzle.id) === value) || null)
  }
  const selectAiStyle = (value) => {
    setAiStyle(value)
    setGenerated(null)
  }
  const create = async (body) => {
    if (creating) return
    setCreating(true)
    setError('')
    try {
      const data = await post('/rooms/create', body)
      nav(`/room/${data.room_id}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }
  const createBtnClass = `pixel-primary${creating ? ' loading' : ''}`
  const createLabel = creating ? '创建中…' : '创建'
  const generate = async () => {
    if (aiGenerating) return
    setAiGenerating(true)
    setError('')
    try {
      setGenerated(await post('/game/generate', { style: aiStyle || undefined }))
    } catch (e) {
      setError(e.message)
    } finally {
      setAiGenerating(false)
    }
  }
  const logout = async () => {
    await logoutToGuest()
    setCedartoyMe(null)
    await load()
  }
  const activeRooms = rooms.filter((room) => room.status === 'waiting' || room.status === 'playing')
  const online = activeRooms.reduce((sum, room) => sum + Number(room.active_players || 0), 0)
  const displayRooms = [...rooms].sort((a, b) => {
    const tier = (r) => r.status === 'finished' ? 2 : (r.ask_count || 0) === 0 ? 1 : 0
    const da = tier(a) - tier(b)
    if (da) return da
    const ta = a.last_active_at || a.created_at || ''
    const tb = b.last_active_at || b.created_at || ''
    return tb.localeCompare(ta)
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
            <select value={selectedPuzzleId} onChange={(event) => selectPuzzle(event.target.value)}>
              <option value="">经典推理题库（可选）</option>
              {puzzles.map((puzzle, i) => (
                <option value={String(puzzle.id)} key={puzzle.id}>
                  #{i + 1} {puzzle.title || puzzle.surface.slice(0, 10)}
                </option>
              ))}
            </select>
          </label>
          <p className="terminal-note">题库抽取的大多微恐，请酌情选择。</p>
          <div className="terminal-preview" aria-live="polite">
            {!random && <p>&gt; 正在等待选题...</p>}
            <p>&gt; 当前题目：<b>{random?.title || '尚未抽取'}</b></p>
            {!random && <p>&gt; 点击标题选择题目。</p>}
            <ColoredSurface className="type-line" text={`> ${random?.surface || '或点击"随机抽题"抽取一碗未解之汤。'}`} />
          </div>
          <div className="actions">
            <button type="button" disabled={randomCooldown > 0} onClick={roll}>{randomCooldown ? `${randomCooldown}s` : '随机抽题'}</button>
            <button type="button" className={createBtnClass} disabled={!random || creating} onClick={() => create({ mode: 'random', puzzle_id: random.id })}>{createLabel}</button>
          </div>
        </div>
      )}
      {createTab === 'custom' && (
        <div className="create-body">
          <label className="terminal-label">汤面<textarea value={custom.surface} onChange={(e) => setCustom({ ...custom, surface: e.target.value })} /></label>
          <label className="terminal-label">
            汤底
            <textarea
              value={custom.answer}
              onChange={(e) => setCustom({ ...custom, answer: e.target.value })}
              placeholder={
                custom.answer.trim()
                  ? undefined
                  : '若需中途公布特殊线索，请用【线索公布】和【线索公布结束】包住公开内容。示例：完整汤底前写【线索公布】一段追加背景【线索公布结束】后再写真实汤底。'
              }
            />
          </label>
          <button type="button" className={`${createBtnClass} wide`} disabled={creating} onClick={() => create({ mode: 'custom', ...custom })}>{createLabel}</button>
        </div>
      )}
      {createTab === 'ai' && (
        <div className="create-body">
          <label className="terminal-label">风格（可选）
            <select value={aiStyle} onChange={(event) => selectAiStyle(event.target.value)}>
              {AI_STYLE_OPTIONS.map(([value, label]) => (
                <option value={value} key={value}>{label}</option>
              ))}
            </select>
          </label>
          <div className="terminal-preview">
            <p>&gt; {aiGenerating ? '生成中...' : '生成器已就绪'}</p>
            <p>&gt; 题目：<b>{generated?.title || '尚未生成'}</b></p>
            <ColoredSurface className="type-line" text={`> ${generated?.surface || '生成提示词可在管理界面的运行参数里调整。'}`} />
          </div>
          <div className="actions">
            <button type="button" disabled={aiGenerating} onClick={generate}>{aiGenerating ? '生成中…' : '生成'}</button>
            <button type="button" className={createBtnClass} disabled={!generated || creating || aiGenerating} onClick={() => create({ mode: 'generated', ...generated })}>{createLabel}</button>
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
        <div className="lobby-title-group">
          <div className="lobby-title"><span className="pixel-mark">▣</span><span>游戏大厅</span></div>
          <div className="lobby-status"><span className="online-dot" /> 在线：<b>{String(Math.max(online, activeRooms.length)).padStart(2, '0')}</b><span>房间：<b>{String(activeRooms.length).padStart(2, '0')}</b></span></div>
        </div>
        <nav className="lobby-actions">
          {me?.is_admin && <Link className="admin-nav-link" to="/add-puzzle"><ListPlus size={17} />加题</Link>}
          {me?.is_admin && <Link className="admin-nav-link" to="/admin"><Shield size={17} />管理</Link>}
          {!me || me?.is_guest ? (
            <button type="button" className="avatar-pill" aria-label="登录" onClick={() => setLoginOpen(true)}>{initials(me)}</button>
          ) : (
            <button type="button" className="avatar-pill" aria-label="我的" onClick={openMine}>{initials(me)}</button>
          )}
          {!me?.is_guest && <button type="button" className="soup-logout-link" onClick={logout} aria-label="退出"><LogOut size={17} /><span>退出</span></button>}
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
        {bottomTab === 'mine' && (
          <section className="lobby-view">
            <div className="rooms-head">
              <h1><span>☻</span>我的</h1>
              <button type="button" onClick={() => setBottomTab('rooms')}>返回房间列表</button>
            </div>
            <div className="panel pixel-view-panel">
              <p>当前用户：{me?.username || `游客${me?.id || ''}`}</p>
              {me?.is_admin && (
                <div className="profile-admin-actions" aria-label="管理员入口">
                  <Link className="enter-room profile-enter" to="/add-puzzle"><ListPlus size={16} />加题</Link>
                  <Link className="enter-room profile-enter" to="/admin"><Shield size={16} />管理后台</Link>
                </div>
              )}
              <Link className="enter-room profile-enter" to="/profile">进入个人页 →</Link>
            </div>
          </section>
        )}
        {bottomTab === 'rooms' && <section className="rooms-area">
          <div className="rooms-head">
            <h1><span>☷</span>活跃房间</h1>
            <button type="button" className="rooms-refresh" onClick={load} aria-label="刷新"><RefreshCw size={16} /></button>
            <button type="button" onClick={load}>按活跃排序</button>
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
                <Link className="pixel-room-card" to={`/room/${room.id}`} key={room.id} onClick={(e) => {
                  if (room.status === 'finished') {
                    e.preventDefault()
                    setSpoilerConfirm(room)
                  }
                }}>
                  <div className="room-code">房间 #{room.id}</div>
                  <div className="room-glyph" aria-hidden="true">?</div>
                  <div className="room-copy">
                    <h2>{roomTitle(room)}</h2>
                    <ColoredSurface text={room.surface} />
                    <div className="room-footer">
                      <div className="room-meta">
                        {tags.map((tag) => <span className="soup-badge" key={tag}>{tag}</span>)}
                        <span className={`soup-badge ${room.status === 'finished' ? 'pale' : 'playing'}`}>{room.status === 'finished' ? '已结束' : '进行中'}</span>
                      </div>
                      <div className="room-stats"><span>提问 {room.ask_count || 0}</span><span>在房 {room.active_players || 0}</span></div>
                      <span className="enter-room" aria-hidden="true">进入 →</span>
                    </div>
                  </div>
                </Link>
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
          ['rooms', <House size={22} />, '大厅'],
          ['leaderboard', <Trophy size={22} />, '排行榜'],
          ['history', <History size={22} />, '历史'],
          ['mine', <UserRound size={22} />, '我的'],
        ].map(([id, icon, label]) => (
          <button type="button" className={bottomTab === id ? 'active' : ''} key={id} onClick={() => {
            if (id === 'mine') { openMine(); return }
            if (id === 'history') {
              setHistoryOpen(true)
              return
            }
            setBottomTab(id)
          }}><span className="bottom-icon">{icon}</span><span>{label}</span></button>
        ))}
      </nav>
      <button className="create-fab" type="button" onClick={() => setDrawerOpen(true)} aria-label="创建房间"><Plus size={20} /></button>
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
      <MineDrawer
        open={mineOpen}
        onClose={() => setMineOpen(false)}
        cedartoyMe={cedartoyMe}
        onLogin={() => setLoginOpen(true)}
        onBind={() => setBindOpen(true)}
        onLogout={logout}
        onUnbind={unbindAi}
      />
      <BindModal
        open={bindOpen}
        onClose={() => setBindOpen(false)}
        onSuccess={loadCedartoyMe}
      />
      <PlaceholderModal
        open={historyOpen}
        title="历史"
        message="游玩历史筹备中，敬请期待。"
        onClose={() => setHistoryOpen(false)}
      />
      {spoilerConfirm && (
        <div className="modal-backdrop room-close-backdrop" onClick={() => setSpoilerConfirm(null)}>
          <div className="modal room-close-modal" role="dialog" aria-modal="true" aria-label="剧透提醒" onClick={(event) => event.stopPropagation()}>
            <h2>⚠️ 剧透提醒</h2>
            <p>该房间已结束，进入后可能显示汤底答案，涉及剧透。确认进入？</p>
            <div className="room-close-actions">
              <button type="button" onClick={() => setSpoilerConfirm(null)}>取消</button>
              <button type="button" className="pixel-primary" onClick={() => {
                const room = spoilerConfirm
                setSpoilerConfirm(null)
                nav(`/room/${room.id}`)
              }}>确认进入</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
