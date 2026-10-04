import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ListPlus,
  LogOut,
  Shield,
} from 'lucide-react'
import { api, ensureGuestToken, getToken, logoutToGuest, post } from '../api'
import BindModal from '../components/BindModal.jsx'
import ColoredSurface from '../components/ColoredSurface.jsx'
import GameLog from '../components/GameLog.jsx'
import LoginModal from '../components/LoginModal.jsx'
import MineDrawer from '../components/MineDrawer.jsx'
import NoteBoard from '../components/NoteBoard.jsx'
import RoomIdCopy from '../components/RoomIdCopy.jsx'
import { soupName } from '../utils/display.js'

const ASK_QUOTA_MESSAGE = '今日个人次数已达上限（300 次），0 点重置'

function parseTags(tags) {
  if (!tags) return []
  if (Array.isArray(tags)) return tags.filter(Boolean)
  return String(tags).split(/[,，、\s]+/).map((tag) => tag.trim()).filter(Boolean)
}

function initials(player) {
  const name = player?.username || 'A'
  return name.slice(0, 1).toUpperCase()
}

function upsertLog(items, entry) {
  if (!entry?.id) return [...items, entry]
  const idx = items.findIndex((row) => row.id === entry.id)
  if (idx === -1) return [...items, entry]
  const next = [...items]
  next[idx] = { ...next[idx], ...entry }
  return next
}

function isNearScrollBottom(element) {
  if (!element) return true
  return element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

export default function Room() {
  const { roomId } = useParams()
  const navigate = useNavigate()
  const [room, setRoom] = useState(null)
  const [askQuota, setAskQuota] = useState(null)
  const quotaRequestRef = useRef(0)
  const [loadedRoomId, setLoadedRoomId] = useState(null)
  const [logs, setLogs] = useState([])
  const [notes, setNotes] = useState([])
  const [content, setContent] = useState('')
  const [inputMode, setInputMode] = useState('ask')
  const [hintLoading, setHintLoading] = useState(false)
  const [hintBusy, setHintBusy] = useState(false)
  const [sendLoading, setSendLoading] = useState(false)
  const [me, setMe] = useState(null)
  const [loginOpen, setLoginOpen] = useState(false)
  const [notesOpen, setNotesOpen] = useState(false)
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false)
  const [closeLoading, setCloseLoading] = useState(false)
  const [surfaceCollapsed, setSurfaceCollapsed] = useState(false)
  const [answerRevealCollapsed, setAnswerRevealCollapsed] = useState(false)
  const [hintConfirmOpen, setHintConfirmOpen] = useState(false)
  const [revealConfirmOpen, setRevealConfirmOpen] = useState(false)
  const [revealFinalConfirmOpen, setRevealFinalConfirmOpen] = useState(false)
  const [revealLoading, setRevealLoading] = useState(false)
  const [revealPromptMilestone, setRevealPromptMilestone] = useState(0)
  const [answerRevealPromptCount, setAnswerRevealPromptCount] = useState(100)
  const [mineOpen, setMineOpen] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [cedartoyMe, setCedartoyMe] = useState(null)
  const [loadError, setLoadError] = useState('')
  const logRef = useRef(null)
  const followLogRef = useRef(true)
  const adminReadonly = Boolean(room?.admin_readonly)

  const refreshQuota = useCallback(async () => {
    const request = ++quotaRequestRef.current
    const token = getToken()
    try {
      const quota = await api('/game/ask-quota')
      if (request === quotaRequestRef.current && token === getToken()) setAskQuota(quota)
    } catch { /* Keep the last known quota; retry on focus, timer or next action. */ }
  }, [])

  const handleQuotaError = (err) => {
    if (err.status !== 429 || err.message !== ASK_QUOTA_MESSAGE) return false
    setAskQuota((current) => ({ ...current, used: 300, limit: 300 }))
    return true
  }

  const load = async () => {
    await ensureGuestToken()
    const [data, profile] = await Promise.all([
      api(`/rooms/${roomId}`),
      api('/auth/me').catch(() => null),
    ])
    setRoom(data)
    ++quotaRequestRef.current
    setAskQuota(data.ask_quota || null)
    setLoadedRoomId(roomId)
    setLogs(data.logs || [])
    setNotes(data.notes || [])
    setMe(profile?.player || null)
  }

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
    setLoadError('')
    load().catch((err) => setLoadError(err.message || '加载房间失败'))
    api('/game/public-settings')
      .then((settings) => {
        const count = Number(settings.answer_reveal_prompt_count || 100)
        setAnswerRevealPromptCount(count > 0 ? count : 100)
      })
      .catch(() => {})
  }, [roomId])

  useEffect(() => {
    if (!me || loadedRoomId !== roomId || adminReadonly) return
    const refreshVisible = () => { if (!document.hidden) refreshQuota() }
    const interval = setInterval(refreshVisible, 30000)
    const resetDelay = Date.parse(askQuota?.reset_at) - Date.now()
    const midnight = Number.isFinite(resetDelay)
      ? setTimeout(refreshQuota, Math.max(1000, resetDelay + 100)) : null
    window.addEventListener('focus', refreshVisible)
    document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      clearInterval(interval)
      clearTimeout(midnight)
      window.removeEventListener('focus', refreshVisible)
      document.removeEventListener('visibilitychange', refreshVisible)
      ++quotaRequestRef.current
    }
  }, [me?.id, loadedRoomId, roomId, adminReadonly, askQuota?.reset_at, refreshQuota])

  useEffect(() => {
    if (!room || loadedRoomId !== roomId || loadError || adminReadonly) return
    let es
    let cancelled = false
    ;(async () => {
      await ensureGuestToken()
      if (cancelled) return
      const token = encodeURIComponent(getToken())
      es = new EventSource(`/soup/api/sse/${roomId}?token=${token}`)
      es.addEventListener('new_log', (event) => {
        const entry = JSON.parse(event.data)
        setLogs((items) => upsertLog(items, entry))
      })
      es.addEventListener('hint_offer', (event) => {
        const data = JSON.parse(event.data)
        setLogs((items) => upsertLog(items, {
          id: data.log_id,
          player_id: data.player_id,
          type: 'hint_offer',
          hint_text: data.hint_text,
          content: data.hint_text,
          resolved: Number(data.resolved ?? 1),
          room_id: roomId,
          username: data.username,
          is_guest: data.is_guest,
          is_ai: data.is_ai,
        }))
      })
      es.addEventListener('hint_resolved', (event) => {
        const data = JSON.parse(event.data)
        setLogs((items) => items.map((row) => (
          Number(row.id) === Number(data.log_id)
            ? {
                ...row,
                resolved: 1,
                hint_accepted: Boolean(data.accept),
                hint_text: data.accept ? (data.hint_text || row.hint_text) : row.hint_text,
              }
            : row
        )))
      })
      es.addEventListener('game_over', (event) => {
        const data = JSON.parse(event.data)
        setRoom((current) => ({ ...current, status: 'finished', answer: data.answer }))
        setRevealConfirmOpen(false)
        setRevealFinalConfirmOpen(false)
      })
      es.addEventListener('new_note', (event) => {
        const data = JSON.parse(event.data)
        setNotes((items) => (
          items.some((note) => Number(note.id) === Number(data.id)) ? items : [...items, data]
        ))
      })
      es.addEventListener('update_note', (event) => {
        const data = JSON.parse(event.data)
        setNotes((items) => items.map((note) => (note.id === data.id ? data : note)))
      })
      es.addEventListener('delete_note', (event) => {
        const data = JSON.parse(event.data)
        setNotes((items) => items.filter((note) => note.id !== data.id))
      })
    })().catch((err) => { if (!cancelled) setLoadError(err.message || '加载房间失败') })
    return () => {
      cancelled = true
      es?.close()
    }
  }, [roomId, loadedRoomId, loadError, adminReadonly])

  useLayoutEffect(() => {
    const stream = logRef.current
    if (!stream || !followLogRef.current) return
    stream.scrollTo({ top: stream.scrollHeight, behavior: 'smooth' })
  }, [logs])

  const pendingHint = logs.some((row) => (
    row.type === 'hint_offer'
    && Number(row.resolved) !== 1
    && Number(row.player_id) === Number(me?.id)
  ))
  const manualUsed = Number(room?.manual_hint_count || 0)
  const hintRemaining = Math.max(0, 3 - manualUsed)
  const finished = room?.status === 'finished'
  const answerRevealed = Boolean(room?.answer_revealed)
  const actionLocked = adminReadonly || finished || answerRevealed
  const quotaExhausted = askQuota?.limit != null && askQuota.used >= askQuota.limit
  // Product policy locks the whole web composer; backend quota only counts asks.
  const composerLocked = actionLocked || quotaExhausted
  const hintDisabled = composerLocked || hintRemaining <= 0 || pendingHint || hintLoading
  const askCount = Math.max(Number(room?.ask_count || 0), logs.filter((row) => row.type === 'ask').length)
  const revealPromptProgressKey = `answer_reveal_prompt_last_${roomId}`
  const answerRevealPromptVisible = !adminReadonly && revealConfirmOpen && !revealFinalConfirmOpen

  useEffect(() => {
    if (!answerRevealPromptVisible) setAnswerRevealCollapsed(false)
  }, [answerRevealPromptVisible])

  useEffect(() => {
    if (!room || loadedRoomId !== roomId || actionLocked || answerRevealPromptCount <= 0 || askCount <= 0) return
    const milestone = Math.floor(askCount / answerRevealPromptCount) * answerRevealPromptCount
    if (milestone <= 0) return
    const promptedCount = Number(localStorage.getItem(revealPromptProgressKey) || 0)
    if (promptedCount >= milestone) return
    setRevealPromptMilestone(milestone)
    setRevealConfirmOpen(true)
  }, [room, loadedRoomId, actionLocked, askCount, answerRevealPromptCount, revealPromptProgressKey, roomId])

  const send = async () => {
    if (!content.trim() || composerLocked || sendLoading) return
    const kind = inputMode === 'guess' ? 'guess' : 'ask'
    followLogRef.current = true
    setSendLoading(true)
    try {
      const entry = await post(`/game/${kind}`, { room_id: roomId, content })
      setLogs((items) => {
        const next = upsertLog(items, entry)
        return entry.result_log ? upsertLog(next, entry.result_log) : next
      })
      if (!entry.system_error) {
        setContent('')
      }
    } catch (err) {
      if (!handleQuotaError(err)) alert(err.message || '发送失败')
    } finally {
      await refreshQuota()
      setSendLoading(false)
    }
  }

  const requestHint = async () => {
    if (hintDisabled) return
    setHintConfirmOpen(false)
    followLogRef.current = true
    setHintLoading(true)
    try {
      const data = await post('/game/hint/request', { room_id: roomId })
      setRoom((current) => ({
        ...current,
        manual_hint_count: 3 - data.manual_hint_remaining,
      }))
      if (data.log_id) {
        setLogs((items) => upsertLog(items, {
          id: data.log_id,
          player_id: me?.id,
          type: 'hint_offer',
          hint_text: data.hint_text,
          content: data.hint_text,
          resolved: 1,
          room_id: roomId,
          username: me?.username,
          is_guest: me?.is_guest,
          is_ai: me?.is_ai,
        }))
      }
    } catch (err) {
      if (!handleQuotaError(err)) alert(err.message || '请求提示失败')
    } finally {
      await refreshQuota()
      setHintLoading(false)
    }
  }

  const openHintConfirm = () => {
    if (hintDisabled) return
    setHintConfirmOpen(true)
  }

  const respondHint = async (logId, accept) => {
    if (adminReadonly || hintBusy) return
    setHintBusy(true)
    try {
      await post('/game/hint/respond', { room_id: roomId, log_id: logId, accept })
    } catch (err) {
      alert(err.message || '处理提示失败')
    } finally {
      setHintBusy(false)
    }
  }

  const logout = async () => {
    await logoutToGuest()
    setCedartoyMe(null)
    setMe(null)
    await load()
  }

  const closeRoom = async () => {
    if (adminReadonly || closeLoading) return
    setCloseLoading(true)
    try {
      await post(`/rooms/${roomId}/close`)
      navigate('/')
    } catch (err) {
      alert(err.message || '关闭房间失败')
      setCloseLoading(false)
    }
  }

  const revealAnswer = async () => {
    if (actionLocked || revealLoading) return
    setRevealLoading(true)
    try {
      const entry = await post('/game/reveal-answer', { room_id: roomId, confirm_reveal: true })
      setRoom((current) => ({
        ...current,
        answer_revealed: true,
        revealed_answer: entry.answer,
      }))
      setContent('')
      setRevealConfirmOpen(false)
      setRevealFinalConfirmOpen(false)
    } catch (err) {
      alert(err.message || '公布汤底失败')
    } finally {
      setRevealLoading(false)
    }
  }

  const markRevealPromptHandled = () => {
    if (revealPromptMilestone > 0) {
      localStorage.setItem(revealPromptProgressKey, String(revealPromptMilestone))
    }
  }

  const acceptRevealPrompt = () => {
    markRevealPromptHandled()
    setRevealFinalConfirmOpen(true)
  }

  const rejectRevealPrompt = () => {
    markRevealPromptHandled()
    setRevealConfirmOpen(false)
    setRevealFinalConfirmOpen(false)
  }

  if (loadError) {
    const isLockedRoom = loadError.includes('锁房') || loadError.includes('仅限创建者本人和同一绑定关系')
    const errorMessage = isLockedRoom ? loadError.replace(/^这是锁房[，,:：\s]*/, '') : loadError
    return (
      <div className="toy-modal show room-error-modal">
        <section className="modal-box" aria-labelledby="roomErrorTitle">
          <h2 className="modal-title" id="roomErrorTitle">
            {isLockedRoom ? '🔒 这个房间已上锁' : '无法进入房间'}
          </h2>
          <p className="modal-hint" role="alert">{errorMessage}</p>
          <div className="modal-actions">
            <Link className="pixel-btn" to="/">返回大厅</Link>
          </div>
        </section>
      </div>
    )
  }
  if (!room || loadedRoomId !== roomId) {
    return <div className="room-page loading-screen">加载中…</div>
  }

  const tags = parseTags(room.tags)
  const displayLogs = logs.filter((row) => row.type !== 'hint_accept' && row.type !== 'hint_reject')
  const canCloseRoom = !adminReadonly && !finished && me && (me.is_admin || Number(room.created_by) === Number(me.id))

  return (
    <div className="room-page">
      <header className="lobby-topbar">
        <Link className="lobby-back" to="/" aria-label="返回大厅"><ArrowLeft size={22} /></Link>
        <div className="lobby-title-group">
          <div className="lobby-title"><span className="pixel-mark">▣</span><span>游戏大厅</span></div>
          <div className={`lobby-status${finished ? '' : ' playing'}`}>
            <span className="online-dot" />
            <RoomIdCopy roomId={room.id} isLocked={!!room.is_locked} />
            <span>{finished ? '已结束' : '进行中'}</span>
          </div>
        </div>
        <nav className="lobby-actions">
          {me?.is_admin && <Link className="admin-nav-link" to="/add-puzzle"><ListPlus size={17} />加题</Link>}
          {me?.is_admin && <Link className="admin-nav-link" to="/admin"><Shield size={17} />管理</Link>}
          {canCloseRoom && (
            <button
              type="button"
              className="close-room-btn"
              onClick={() => setCloseConfirmOpen(true)}
            >
              关闭房间
            </button>
          )}
          {!me || me?.is_guest ? (
            <button type="button" className="avatar-pill" aria-label="登录" onClick={() => setLoginOpen(true)}>{initials(me)}</button>
          ) : (
            <button type="button" className="avatar-pill" aria-label="我的" onClick={openMine}>{initials(me)}</button>
          )}
          {!me?.is_guest && <button type="button" className="soup-logout-link" onClick={logout} aria-label="退出"><LogOut size={17} /><span>退出</span></button>}
        </nav>
      </header>

      <div className={`room-main${surfaceCollapsed ? ' surface-collapsed' : ''}`}>
        <aside className={`room-surface-panel${surfaceCollapsed ? ' collapsed' : ''}`}>
          <div className="terminal-head">
            <span className="lights"><i /><i /><i /></span>
            <strong>汤面</strong>
            <div className="surface-head-meta" aria-label="房间状态">
              <span className={`surface-state ${finished ? 'pale' : 'playing'}`}>{finished ? '已结束' : '进行中'}</span>
              <span>提问 {askCount}</span>
              <span>在房 {adminReadonly ? (room.active_players ?? 0) : (room.active_players || 1)}</span>
            </div>
            {canCloseRoom && (
              <button
                type="button"
                className="close-room-btn close-room-btn-surface"
                onClick={() => setCloseConfirmOpen(true)}
              >
                关闭房间
              </button>
            )}
            <button type="button" className="surface-toggle" onClick={() => setSurfaceCollapsed(!surfaceCollapsed)} aria-label={surfaceCollapsed ? '展开汤面' : '收起汤面'}>
              {surfaceCollapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            </button>
          </div>
          <div className="room-surface-scroll">
            <h1>{soupName(room)}</h1>
            <ColoredSurface text={room.surface} />
            <div className="room-meta">
              {tags.map((tag) => <span className="soup-badge" key={tag}>{tag}</span>)}
            </div>
          </div>
        </aside>

        <section className="room-play">
          {adminReadonly && <p className="room-readonly-notice" role="status">管理员只读查看</p>}
          {answerRevealPromptVisible && (
            <div className={`answer-reveal-prompt${answerRevealCollapsed ? ' collapsed' : ''}`} role="region" aria-label="公布汤底提示">
              <div className="answer-reveal-copy">
                <div className="answer-reveal-prompt-label">{answerRevealCollapsed ? '公布汤底' : '> 【公布汤底】'}</div>
                {!answerRevealCollapsed && (
                  <p>
                    本房间已经累计 {askCount} 次提问。是否查看汤底？查看后你将不能继续提问、猜测或操作记事板。
                  </p>
                )}
              </div>
              <button
                type="button"
                className="surface-toggle answer-reveal-toggle"
                onClick={() => setAnswerRevealCollapsed((collapsed) => !collapsed)}
                aria-label={answerRevealCollapsed ? '展开公布汤底' : '收起公布汤底'}
                aria-expanded={!answerRevealCollapsed}
              >
                {answerRevealCollapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
              </button>
              {!answerRevealCollapsed && (
                <div className="answer-reveal-actions">
                  <button type="button" disabled={revealLoading} onClick={rejectRevealPrompt}>
                    拒绝
                  </button>
                  <button type="button" className="pixel-primary" disabled={revealLoading} onClick={acceptRevealPrompt}>
                    接受
                  </button>
                </div>
              )}
            </div>
          )}

          <section className="session-log-panel">
            <div className="terminal-head">
              <span className="lights"><i /><i /><i /></span>
              <strong>侦探日志</strong>
              <small>对话记录</small>
            </div>
            <div
              className="session-log-stream"
              ref={logRef}
              onScroll={(event) => {
                followLogRef.current = isNearScrollBottom(event.currentTarget)
              }}
            >
              <GameLog
                logs={displayLogs}
                roomId={roomId}
                roomStatus={room?.status}
                readOnly={adminReadonly}
                onHintRespond={respondHint}
                hintBusy={hintBusy}
                currentPlayerId={me?.id}
                answerRevealPromptCount={answerRevealPromptCount}
              />
              {!adminReadonly && answerRevealed && (
                <div className="log-game-over private-answer-reveal" role="region" aria-label="已公布汤底" style={{ marginTop: 10 }}>
                  <div className="log-game-over-label">&gt; 已查看汤底</div>
                  <p>{room.revealed_answer || room.answer}</p>
                </div>
              )}
            </div>
          </section>

          {!adminReadonly && (
            <section className="room-composer">
              <div className="composer-head">
                <div className="composer-tabs" role="tablist" aria-label="输入模式">
                  <button
                    type="button"
                    className={inputMode === 'ask' ? 'active' : ''}
                    disabled={composerLocked}
                    onClick={() => setInputMode('ask')}
                  >
                    提问
                  </button>
                  <button
                    type="button"
                    className={inputMode === 'guess' ? 'active' : ''}
                    disabled={composerLocked}
                    onClick={() => setInputMode('guess')}
                  >
                    猜测汤底
                  </button>
                </div>
                <button
                  type="button"
                  className={`hint-request-btn${hintDisabled ? ' exhausted' : ''}`}
                  disabled={hintDisabled}
                  onClick={openHintConfirm}
                >
                  请求提示
                  <span>{hintRemaining}/3</span>
                </button>
              </div>
              <div className="composer-row">
                <textarea
                  maxLength={200}
                  value={quotaExhausted ? '' : content}
                  disabled={composerLocked}
                  aria-label={quotaExhausted ? ASK_QUOTA_MESSAGE : (inputMode === 'guess' ? '猜测汤底' : '提问')}
                  onChange={(event) => setContent(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      send()
                    }
                  }}
                  placeholder={quotaExhausted ? ASK_QUOTA_MESSAGE : (inputMode === 'guess' ? '写下你的汤底猜测…' : '输入你的提问…')}
                  rows={quotaExhausted ? 2 : 1}
                />
                <button
                  type="button"
                  className="pixel-primary send-btn"
                  disabled={composerLocked || !content.trim() || sendLoading}
                  onClick={send}
                >
                  {sendLoading ? '发送中…' : '发送'}
                </button>
              </div>
              {finished && <p className="composer-finished-hint">游戏已结束</p>}
              {!finished && answerRevealed && <p className="composer-finished-hint">你已查看汤底，不能继续答题或操作记事板</p>}
            </section>
          )}
        </section>
      </div>

      {!adminReadonly && (
        <>
          <button
            type="button"
            className="notepad-drawer-tab"
            aria-expanded={notesOpen}
            aria-controls="room-notepad-drawer"
            onClick={() => setNotesOpen(true)}
          >
            <span>📝记事板</span>
          </button>

          <div
            className={`notepad-drawer${notesOpen ? ' show' : ''}`}
            onClick={() => setNotesOpen(false)}
            aria-hidden={!notesOpen}
          >
            <div
              id="room-notepad-drawer"
              className="notepad-drawer-panel"
              role="dialog"
              aria-label="记事板"
              onClick={(event) => event.stopPropagation()}
            >
              <NoteBoard
                roomId={roomId}
                notes={notes}
                setNotes={setNotes}
                currentPlayer={me}
                disabled={actionLocked}
                disabledLabel={answerRevealed ? '你已查看汤底' : '游戏已结束'}
              />
            </div>
          </div>
        </>
      )}

      {!adminReadonly && revealFinalConfirmOpen && (
        <div className="modal-backdrop room-close-backdrop" onClick={() => setRevealFinalConfirmOpen(false)}>
          <div
            className="modal room-close-modal"
            role="dialog"
            aria-modal="true"
            aria-label="最终公布汤底确认"
            onClick={(event) => event.stopPropagation()}
          >
            <h2>确定公布？</h2>
            <p>
              确认后你会立即看到完整汤底。房间不会结束，其他玩家可以继续推理；
              但你不能再参与本局答题，也不能再新增、修改或删除记事板内容。这个操作不能撤回。
            </p>
            <div className="room-close-actions">
              <button type="button" disabled={revealLoading} onClick={() => setRevealFinalConfirmOpen(false)}>
                返回
              </button>
              <button type="button" className="pixel-primary" disabled={revealLoading} onClick={revealAnswer}>
                {revealLoading ? '公布中…' : '确认公布'}
              </button>
            </div>
          </div>
        </div>
      )}

      {!adminReadonly && hintConfirmOpen && (
        <div className="modal-backdrop room-close-backdrop" onClick={() => setHintConfirmOpen(false)}>
          <div
            className="modal room-close-modal"
            role="dialog"
            aria-modal="true"
            aria-label="请求提示确认"
            onClick={(event) => event.stopPropagation()}
          >
            <h2>请求提示？</h2>
            <p>
              将消耗 1 次手动提示机会（剩余 {hintRemaining} 次）。
              系统会给出一条线索，你可以选择接受或拒绝查看。
            </p>
            <div className="room-close-actions">
              <button type="button" disabled={hintLoading} onClick={() => setHintConfirmOpen(false)}>
                取消
              </button>
              <button type="button" className="pixel-primary" disabled={hintDisabled} onClick={requestHint}>
                {hintLoading ? '生成中…' : '确认请求'}
              </button>
            </div>
          </div>
        </div>
      )}

      {!adminReadonly && closeConfirmOpen && (
        <div className="modal-backdrop room-close-backdrop" onClick={() => setCloseConfirmOpen(false)}>
          <div className="modal room-close-modal" role="dialog" aria-modal="true" aria-label="关闭房间确认" onClick={(event) => event.stopPropagation()}>
            <h2>关闭房间？</h2>
            <p>关闭后房间会标记为已结束，玩家将不能继续提问或猜测。</p>
            <div className="room-close-actions">
              <button type="button" disabled={closeLoading} onClick={() => setCloseConfirmOpen(false)}>
                取消
              </button>
              <button type="button" className="pixel-primary" disabled={closeLoading} onClick={closeRoom}>
                {closeLoading ? '关闭中…' : '确认关闭'}
              </button>
            </div>
          </div>
        </div>
      )}

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
    </div>
  )
}
