const TOKEN_KEY = 'turtle_soup_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function api(path, options = {}) {
  const headers = {
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    ...(options.headers || {}),
  }
  const res = await fetch(`/soup/api${path}`, { ...options, headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || '请求失败')
  return data
}

export const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) })
export const put = (path, body) => api(path, { method: 'PUT', body: JSON.stringify(body || {}) })
export const del = (path) => api(path, { method: 'DELETE' })
