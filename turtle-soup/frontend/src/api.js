const TOKEN_KEY = 'turtle_soup_token'
const CEDARTOY_TOKEN_KEY = 'cedartoy_token'
const CEDARTOY_USER_ID_KEY = 'cedartoy_user_id'
const USERNAME_RE = /^[a-zA-Z0-9_\u4e00-\u9fff]{2,20}$/

export function validateLoginInput(username, password) {
  if (username.length < 2 || username.length > 20) return '用户名长度须为 2-20 个字符'
  if (!USERNAME_RE.test(username)) return '用户名只能包含字母、数字、下划线和中文'
  if (password.length < 6) return '密码至少 6 位'
  return ''
}

export async function loginOrRegister(username, password) {
  const res = await fetch('/api/auth/login_or_register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || data.detail || '登录失败')
  localStorage.setItem(CEDARTOY_TOKEN_KEY, data.token)
  localStorage.setItem(CEDARTOY_USER_ID_KEY, String(data.user.id))
  const soupAuth = await post('/auth/guest', { user_id: data.user.id })
  setToken(soupAuth.token)
  return soupAuth.player
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
  localStorage.removeItem(CEDARTOY_TOKEN_KEY)
  localStorage.removeItem(CEDARTOY_USER_ID_KEY)
  clearToken()
  return ensureGuestToken({ forceGuest: true })
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
