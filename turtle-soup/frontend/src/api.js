const TOKEN_KEY = 'turtle_soup_token'
const CEDARTOY_TOKEN_KEY = 'cedartoy_token'
const CEDARTOY_USER_ID_KEY = 'cedartoy_user_id'
const USERNAME_RE = /^[a-zA-Z0-9_\u4e00-\u9fff]{2,20}$/

let authOp = Promise.resolve()

function authLocked(task) {
  const next = authOp.then(task, task)
  authOp = next.catch(() => {})
  return next
}

async function exchangeSoupToken(userId) {
  const res = await fetch('/soup/api/auth/guest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(formatApiDetail(data.detail) || data.error || '海龟汤登录失败')
  }
  return data
}

export function validateLoginInput(username, password) {
  if (!username || !password) return '用户名和密码必填'
  if (username.length < 2 || username.length > 20) return '用户名长度须为 2-20 个字符'
  if (!USERNAME_RE.test(username)) return '用户名只能包含字母、数字、下划线和中文'
  if (password.length < 6) return '密码至少 6 位'
  return ''
}

export async function loginAccount(username, password, mode = 'login') {
  return authLocked(async () => {
    const registering = mode === 'register'
    const res = await fetch(registering ? '/api/auth/register' : '/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || data.detail || (registering ? '注册失败' : '登录失败'))
    const soupAuth = await exchangeSoupToken(data.user.id)
    localStorage.setItem(CEDARTOY_TOKEN_KEY, data.token)
    localStorage.setItem(CEDARTOY_USER_ID_KEY, String(data.user.id))
    setToken(soupAuth.token)
    return soupAuth.player
  })
}

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function ensureGuestToken(options = {}) {
  const { forceGuest = false } = options
  const toyUserId = localStorage.getItem(CEDARTOY_USER_ID_KEY)
  if (toyUserId && !forceGuest) {
    try {
      const data = await post('/auth/guest', { user_id: parseInt(toyUserId) })
      setToken(data.token)
      return data.token
    } catch (e) {
      localStorage.removeItem(CEDARTOY_USER_ID_KEY)
    }
  }
  if (getToken()) return getToken()
  const data = await post('/auth/guest')
  setToken(data.token)
  return data.token
}

export async function logoutToGuest() {
  return authLocked(async () => {
    localStorage.removeItem(CEDARTOY_TOKEN_KEY)
    localStorage.removeItem(CEDARTOY_USER_ID_KEY)
    clearToken()
    return ensureGuestToken({ forceGuest: true })
  })
}

async function fetchSoupMe(token = getToken()) {
  if (!token) return null
  const res = await fetch('/soup/api/auth/me', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) return null
  const data = await res.json().catch(() => ({}))
  return data.player || null
}

async function resolveToyUserId() {
  const stored = localStorage.getItem(CEDARTOY_USER_ID_KEY)
  if (stored) return Number(stored)

  const toyToken = localStorage.getItem(CEDARTOY_TOKEN_KEY)
  if (toyToken) {
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${toyToken}` },
    })
    if (res.ok) {
      const data = await res.json().catch(() => ({}))
      if (data.user?.id) {
        localStorage.setItem(CEDARTOY_USER_ID_KEY, String(data.user.id))
        return Number(data.user.id)
      }
    }
  }

  const player = await fetchSoupMe()
  if (player?.user_id) {
    localStorage.setItem(CEDARTOY_USER_ID_KEY, String(player.user_id))
    return Number(player.user_id)
  }
  return null
}

/** Refresh soup JWT for admin pages; accepts existing admin token or platform account. */
export async function ensureAdminSession() {
  return authLocked(async () => {
    const current = await fetchSoupMe()
    if (current?.is_admin) {
      if (current.user_id) {
        localStorage.setItem(CEDARTOY_USER_ID_KEY, String(current.user_id))
      }
      return current
    }

    const toyUserId = await resolveToyUserId()
    if (!toyUserId) {
      const err = new Error('请先在开始页面登录统一管理员账号')
      err.status = 401
      throw err
    }

    const soupAuth = await exchangeSoupToken(toyUserId)
    setToken(soupAuth.token)
    const player = soupAuth.player
    if (!player?.is_admin) {
      const err = new Error('当前统一账号没有管理员权限')
      err.status = 403
      throw err
    }
    return player
  })
}

function formatApiDetail(detail) {
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join('；')
  }
  if (typeof detail === 'string' && detail) return detail
  return '请求失败'
}

export async function api(path, options = {}) {
  const { __retried, ...fetchOptions } = options
  const headers = {
    ...(fetchOptions.body ? { 'Content-Type': 'application/json' } : {}),
    ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    ...(fetchOptions.headers || {}),
  }
  const res = await fetch(`/soup/api${path}`, { ...fetchOptions, headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401 && !__retried && path !== '/auth/guest') {
      clearToken()
      await ensureGuestToken()
      return api(path, { ...fetchOptions, __retried: true })
    }
    const error = new Error(formatApiDetail(data.detail))
    error.status = res.status
    throw error
  }
  return data
}

export const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) })
export const put = (path, body) => api(path, { method: 'PUT', body: JSON.stringify(body || {}) })
export const del = (path) => api(path, { method: 'DELETE' })
