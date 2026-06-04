import { useEffect, useMemo, useState } from 'react'
import { api, post, put, del, ensureAdminSession } from '../api'
import { formatDbDateTime } from '../utils/display.js'

const tableNames = {
  overview: '总览',
  puzzles: '题库',
  submissions: '投稿',
  players: '玩家',
  rooms: '房间',
  bans: '封禁',
  reports: '举报',
  flags: '风控',
  'api-configs': '裁判 API',
  settings: '系统设置',
  puzzle_submissions: '投稿',
  flagged_content: '风控',
}

const tabOrder = ['overview', 'puzzles', 'submissions', 'players', 'rooms', 'bans', 'reports', 'flags', 'api-configs', 'settings']

const fieldLabels = {
  id: 'ID',
  title: '汤名',
  surface: '汤面',
  answer: '汤底',
  tags: '标签',
  enabled: '启用',
  username: '用户名',
  is_guest: '游客',
  is_ai: 'AI',
  is_admin: '管理员',
  source: '来源',
  user_id: '统一账号',
  ask_count: '提问数',
  win_count: '胜利数',
  game_count: '局数',
  created_at: '创建时间',
  last_active_at: '活跃时间',
  status: '状态',
  created_by: '创建者',
  winner_id: '胜者',
  finished_at: '结束时间',
  submitted_by: '投稿人',
  password_hash: '密码哈希',
  reporter_id: '举报人',
  target_player_id: '被举报人',
  room_id: '房间',
  log_id: '记录',
  reason: '原因',
  type: '类型',
  ref_id: '对象 ID',
  ip: 'IP',
  banned_by: '封禁人',
  name: '名称',
  api_url: '接口地址',
  api_key: '密钥',
  model: '模型',
  priority: '优先级',
  key: '配置项',
  value: '值',
  description: '说明',
}

const tabDescriptions = {
  overview: '查看海龟汤内部数据量。这里的玩家是身份记录，不等同于当前在线人数。',
  puzzles: '正式题库。启用的题目会被大厅随机抽题使用。',
  submissions: '玩家自填或生成后提交的候选题，可收录进正式题库。',
  players: '海龟汤内部身份记录。游客访问、统一账号进入、MCP/AI 调用都会创建或更新这里的记录，不代表当前正在玩的人。',
  rooms: '已经创建过的房间，包括等待中、进行中和已结束的对局。',
  bans: '按 IP 封禁访问，命中后会阻止继续使用海龟汤。',
  reports: '玩家对房间发言或用户的举报记录。',
  flags: '系统或管理员标记的风险内容，用于后续处理。',
  'api-configs': '裁判 LLM 的接口配置。按优先级排序后轮转调度；停用或连续失败 5 次后不会被调度。保存后可点「测试」探测连通性。',
  settings: '运行参数。修改后会影响新请求或后续流程。',
}

const settingDescriptions = {
  max_rooms: '大厅允许同时保留的活跃房间数量上限。',
  hint_trigger_count: '距上次提示后再 ask 多少条触发自动提示。',
  ai_cooldown_questions: 'AI 裁判相关操作按提问数计算的冷却间隔。',
  ai_cooldown_seconds: 'AI 裁判请求之间的最短秒级间隔。',
  generate_cooldown_seconds: 'AI 生成题目的按钮冷却秒数。',
  generate_prompt: 'AI 生成题目使用的提示词。该请求使用裁判 API 配置。',
  judge_prompt: '裁判判断提问时使用的系统提示词。',
  judge_prompt_clue: '线索汤专用补充提示词。汤底中用【线索公布】和【线索公布结束】包住中途公开内容。',
  guest_expire_hours: '游客身份/临时数据可保留的小时数。',
  room_inactive_expire_hours: '房间最后一条玩家发言后自动结束的小时数。',
  finished_room_retention_hours: '已结束房间继续保留的小时数。',
}

const statusText = {
  pending: '待处理',
  added: '已收录',
  ignored: '已忽略',
  resolved: '已处理',
  waiting: '等待中',
  playing: '进行中',
  finished: '已结束',
}

const columns = {
  puzzles: ['id', 'title', 'surface', 'answer', 'tags', 'enabled', 'created_at'],
  submissions: ['id', 'surface', 'answer', 'tags', 'status', 'submitted_by', 'created_at'],
  players: ['id', 'username', 'user_id', 'is_guest', 'is_ai', 'is_admin', 'source', 'ask_count', 'win_count', 'game_count', 'last_active_at'],
  rooms: ['id', 'surface', 'answer', 'status', 'created_by', 'winner_id', 'created_at', 'finished_at'],
  bans: ['id', 'ip', 'reason', 'banned_by', 'created_at'],
  reports: ['id', 'reporter_id', 'target_player_id', 'room_id', 'log_id', 'reason', 'status', 'created_at'],
  flags: ['id', 'type', 'ref_id', 'reason', 'status', 'created_at'],
  'api-configs': ['id', 'name', 'api_url', 'api_key', 'model', 'enabled', 'priority', 'created_at'],
  settings: ['key', 'value', 'description'],
}

const formFields = {
  puzzles: [
    { key: 'title', label: '汤名' },
    { key: 'surface', label: '汤面', type: 'textarea', required: true },
    { key: 'answer', label: '汤底', type: 'textarea', required: true },
    { key: 'tags', label: '标签' },
  ],
  submissions: [
    { key: 'surface', label: '汤面', type: 'textarea', required: true },
    { key: 'answer', label: '汤底', type: 'textarea', required: true },
    { key: 'tags', label: '标签' },
    { key: 'status', label: '状态', type: 'select', options: ['pending', 'added', 'ignored'] },
  ],
  players: [
    { key: 'username', label: '用户名' },
    { key: 'is_guest', label: '游客', type: 'checkbox' },
    { key: 'is_ai', label: 'AI', type: 'checkbox' },
    { key: 'is_admin', label: '管理员', type: 'checkbox' },
    { key: 'source', label: '来源', type: 'select', options: ['web', 'mcp'] },
  ],
  rooms: [
    { key: 'surface', label: '汤面', type: 'textarea', required: true },
    { key: 'answer', label: '汤底', type: 'textarea', required: true },
    { key: 'status', label: '状态', type: 'select', options: ['waiting', 'playing', 'finished'] },
    { key: 'winner_id', label: '胜者 ID', type: 'number' },
  ],
  bans: [
    { key: 'ip', label: 'IP', required: true },
    { key: 'reason', label: '原因', type: 'textarea' },
  ],
  reports: [
    { key: 'reporter_id', label: '举报人 ID', type: 'number' },
    { key: 'target_player_id', label: '被举报人 ID', type: 'number' },
    { key: 'room_id', label: '房间 ID' },
    { key: 'log_id', label: '记录 ID', type: 'number' },
    { key: 'reason', label: '原因', type: 'textarea' },
    { key: 'status', label: '状态', type: 'select', options: ['pending', 'resolved'] },
  ],
  flags: [
    { key: 'type', label: '类型', required: true },
    { key: 'ref_id', label: '对象 ID', type: 'number', required: true },
    { key: 'reason', label: '原因', type: 'textarea' },
    { key: 'status', label: '状态', type: 'select', options: ['pending', 'resolved'] },
  ],
  'api-configs': [
    { key: 'name', label: '名称', required: true },
    { key: 'api_url', label: '接口地址', required: true },
    { key: 'api_key', label: '密钥', secretOnEdit: true },
    { key: 'model', label: '模型', required: true },
    { key: 'enabled', label: '启用', type: 'checkbox' },
    { key: 'priority', label: '优先级', type: 'number' },
  ],
  settings: [
    { key: 'key', label: '配置项', required: true, readOnlyOnEdit: true },
    { key: 'value', label: '值', required: true, type: 'textarea' },
  ],
}

const defaults = {
  puzzles: { title: '', surface: '', answer: '', tags: '' },
  submissions: { surface: '', answer: '', tags: '', status: 'pending' },
  players: { username: '', is_guest: 0, is_ai: 0, is_admin: 0, source: 'web' },
  rooms: { surface: '', answer: '', status: 'waiting', winner_id: '' },
  bans: { ip: '', reason: '' },
  reports: { reporter_id: '', target_player_id: '', room_id: '', log_id: '', reason: '', status: 'pending' },
  flags: { type: 'manual', ref_id: 0, reason: '', status: 'pending' },
  'api-configs': { name: '', api_url: '', api_key: '', model: '', enabled: 1, priority: 0 },
  settings: { key: '', value: '' },
}

const canAdd = new Set(['puzzles', 'submissions', 'players', 'rooms', 'bans', 'reports', 'flags', 'api-configs', 'settings'])
const canEdit = new Set(['puzzles', 'submissions', 'players', 'rooms', 'bans', 'reports', 'flags', 'api-configs', 'settings'])
const canDelete = new Set(['puzzles', 'submissions', 'players', 'rooms', 'bans', 'reports', 'flags', 'api-configs', 'settings'])

const timestampFields = new Set(['created_at', 'last_active_at', 'finished_at', 'updated_at', 'joined_at', 'expires_at', 'deleted_at', 'bound_at'])

function displayValue(key, value) {
  if (value === null || value === undefined || value === '') return '未填写'
  if (['enabled', 'is_guest', 'is_ai', 'is_admin'].includes(key)) return Number(value) ? '是' : '否'
  if (key === 'status') return statusText[value] || value
  if (timestampFields.has(key)) return formatDbDateTime(value)
  if (Array.isArray(value)) return value.map((item, index) => `${index + 1}. ${displayValue('', item)}`).join('\n')
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([itemKey, itemValue]) => `${fieldLabels[itemKey] || tableNames[itemKey] || itemKey}：${displayValue(itemKey, itemValue)}`)
      .join('\n')
  }
  return String(value)
}

function cellValue(tab, row, key) {
  if (key === 'description' && tab === 'settings') return settingDescriptions[row.key] || '自定义配置项'
  if (key === 'username' && tab === 'players' && !row[key]) return row.is_guest ? `游客${row.id}` : `玩家${row.id}`
  if (key === 'source') return row[key] === 'mcp' ? 'MCP / AI' : '网页'
  return row[key]
}

function clip(value) {
  const text = displayValue('', value)
  return text.length > 80 ? `${text.slice(0, 80)}...` : text
}

function rowKey(tab, row) {
  return `${tab}-${row.id ?? row.key}`
}

function formatTestResultBody(data, reply) {
  const lines = []
  if (data?.config_name || data?.model) {
    lines.push(`配置：${data.config_name || '—'} · 模型 ${data.model || '—'}`)
  }
  if (data?.http_status != null) {
    lines.push(`HTTP：${data.http_status}`)
  }
  if (data?.llm_ms != null) {
    lines.push(`耗时：${data.llm_ms}ms`)
  }
  if (lines.length) lines.push('')
  lines.push(reply || '(无文本回复)')
  return lines.join('\n')
}

function TestResultBlock({ result, onClose, className = '' }) {
  if (!result) return null
  return (
    <div className={`cfg-test-result ${result.success ? 'cfg-test-result--ok' : 'cfg-test-result--err'} ${className}`.trim()}>
      <div className="cfg-test-result-head">
        <div className="cfg-test-result-title">{result.title}</div>
        <button type="button" className="cfg-test-result-close" onClick={onClose} aria-label="关闭">关闭</button>
      </div>
      <pre className="cfg-test-result-body">{result.body}</pre>
    </div>
  )
}

async function runApiConfigTest(configId, payload = null) {
  const data = payload
    ? await post('/admin/api-configs/test', payload)
    : await post(`/admin/api-configs/${configId}/test`)
  const reply = data.data?.reply || ''
  const rawText = data.data?.raw ? JSON.stringify(data.data.raw, null, 2) : ''
  if (data.success) {
    return {
      success: true,
      title: '测试成功',
      body: formatTestResultBody(data.data, reply),
    }
  }
  const errDetail = rawText.slice(0, 2000) || data.message || '(无详情)'
  return {
    success: false,
    title: '测试失败',
    body: formatTestResultBody(data.data, errDetail),
  }
}

async function runApiModelFetch(payload) {
  const data = await post('/admin/api-configs/models', payload)
  const models = Array.isArray(data.models) ? data.models : []
  const body = data.success
    ? models.join('\n') || '(没有返回可用模型)'
    : JSON.stringify(data.raw || data.message || '(无详情)', null, 2).slice(0, 2000)
  return {
    success: Boolean(data.success),
    title: data.success ? `拉取成功 · ${models.length} 个模型` : '拉取失败',
    body,
    models,
  }
}

export default function Admin() {
  const [tab, setTab] = useState('overview')
  const [rows, setRows] = useState(null)
  const [query, setQuery] = useState('')
  const [modal, setModal] = useState(null)
  const [draft, setDraft] = useState({})
  const [error, setError] = useState('')
  const [authError, setAuthError] = useState(null)
  const [testingConfigId, setTestingConfigId] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [modalError, setModalError] = useState('')

  const load = async () => {
    try {
      await ensureAdminSession()
      const path = tab === 'puzzles' ? '/puzzles/' : `/admin/${tab}`
      const data = await api(path)
      setRows(data)
      setError('')
      setAuthError(null)
    } catch (e) {
      if (e.status === 401 || e.status === 403) {
        setRows(null)
        setError('')
        setAuthError({ status: e.status, message: e.message })
        return
      }
      setError(e.message)
    }
  }

  useEffect(() => { document.title = `海龟汤管理后台 - ${tableNames[tab]}` }, [tab])

  useEffect(() => { load() }, [tab])

  const filteredRows = useMemo(() => {
    if (!Array.isArray(rows)) return []
    const needle = query.trim().toLowerCase()
    if (!needle) return rows
    const cols = columns[tab] || Object.keys(rows[0] || {})
    return rows.filter((row) => cols.some((key) => displayValue('', cellValue(tab, row, key)).toLowerCase().includes(needle)))
  }, [rows, query, tab])

  const openAdd = () => {
    setModalError('')
    setDraft({ ...(defaults[tab] || {}) })
    setModal({ mode: 'add', tab, title: `新增${tableNames[tab]}` })
  }

  const openView = async (row) => {
    const detail = await detailRow(row)
    setDraft(detail)
    setModal({ mode: 'view', tab, title: `查看${tableNames[tab]}` })
  }

  const openEdit = async (row) => {
    setModalError('')
    const detail = await detailRow(row)
    const normalized = tab === 'api-configs' ? { ...detail, api_key: '' } : detail
    setDraft({ ...(defaults[tab] || {}), ...normalized })
    setModal({ mode: 'edit', tab, title: `编辑${tableNames[tab]}` })
  }

  const detailRow = async (row) => {
    if (tab === 'puzzles') return await api(`/puzzles/${row.id}`)
    return row
  }

  const removeRow = async (row) => {
    if (!confirm(`确认删除这条${tableNames[tab]}记录？`)) return
    try {
      setError('')
      const rowId = encodeURIComponent(String(row.id ?? row.key ?? ''))
      if (tab === 'puzzles') await del(`/puzzles/${rowId}`)
      else if (tab === 'settings') await del(`/admin/settings/${encodeURIComponent(row.key)}`)
      else await del(`/admin/${tab}/${rowId}`)
      await load()
    } catch (e) {
      if (e.status === 401 || e.status === 403) {
        setAuthError({ status: e.status, message: e.message })
        return
      }
      setError(e.message || '删除失败')
    }
  }

  const runAction = async (name, row) => {
    try {
      setError('')
      const rowId = encodeURIComponent(String(row.id ?? ''))
      if (name === 'togglePuzzle') await api(`/puzzles/${rowId}/toggle`, { method: 'PATCH' })
      if (name === 'addSubmission') await post(`/admin/submissions/${rowId}/add`, row)
      if (name === 'ignoreSubmission') await post(`/admin/submissions/${rowId}/ignore`)
      if (name === 'resetPlayer') await post(`/admin/players/${rowId}/reset`)
      if (name === 'toggleAdmin') await api(`/admin/players/${rowId}/admin?enabled=${row.is_admin ? 0 : 1}`, { method: 'PATCH' })
      if (name === 'finishRoom') await post(`/admin/rooms/${rowId}/finish`)
      if (name === 'resolveReport') await post(`/admin/reports/${rowId}/resolve`)
      if (name === 'resolveFlag') await post(`/admin/flags/${rowId}/resolve`)
      await load()
    } catch (e) {
      if (e.status === 401 || e.status === 403) {
        setAuthError({ status: e.status, message: e.message })
        return
      }
      setError(e.message || '操作失败')
    }
  }

  const handleTestConfig = async (row) => {
    setTestingConfigId(row.id)
    setTestResult(null)
    try {
      const result = await runApiConfigTest(row.id)
      setTestResult({ configId: row.id, ...result })
    } catch (e) {
      setTestResult({
        configId: row.id,
        success: false,
        title: '网络错误',
        body: e.message || '请检查接口地址、密钥或模型',
      })
    } finally {
      setTestingConfigId(null)
    }
  }

  const saveModal = async () => {
    setModalError('')
    try {
      const payload = serializeDraft(tab, draft)
      if (modal.mode === 'add') {
        if (tab === 'puzzles') await post('/puzzles/', payload)
        else if (tab === 'settings') await put(`/admin/settings/${encodeURIComponent(payload.key)}`, { value: payload.value })
        else await post(`/admin/${tab}`, payload)
      } else {
        if (tab === 'puzzles') await put(`/puzzles/${draft.id}`, payload)
        else if (tab === 'settings') await put(`/admin/settings/${encodeURIComponent(draft.key)}`, { value: payload.value })
        else await put(`/admin/${tab}/${draft.id}`, payload)
      }
      setModal(null)
      await load()
    } catch (e) {
      setModalError(formatApiError(e))
    }
  }

  return (
    <section className="admin-page">
      <div className="admin-heading">
        <div>
          <h2>管理后台</h2>
          <p>{tabDescriptions[tab]}</p>
        </div>
        {tab !== 'overview' && <input className="admin-search" placeholder={`查找${tableNames[tab]}`} value={query} onChange={(e) => setQuery(e.target.value)} />}
      </div>
      {authError ? <AdminAuthError error={authError} /> : (
        <>
          <div className="tabs">{tabOrder.map((t) => <button className={tab === t ? 'active' : ''} key={t} onClick={() => { setTab(t); setQuery('') }}>{tableNames[t]}</button>)}</div>
          <div className="admin-toolbar">
            <b>{tableNames[tab]}</b>
            <span>{Array.isArray(rows) ? `${filteredRows.length} 条记录` : '系统概览'}</span>
            {canAdd.has(tab) && <button className="primary" onClick={openAdd}>新增</button>}
            <button onClick={load}>刷新</button>
          </div>
          {error && <p className="error">{error}</p>}
          {tab === 'api-configs' && testResult && (
            <TestResultBlock
              result={testResult}
              onClose={() => setTestResult(null)}
              className="cfg-test-result--panel"
            />
          )}
          {tab === 'overview' ? <Overview data={rows} onOpen={(nextTab) => setTab(nextTab)} /> : (
            <AdminTable
              tab={tab}
              rows={filteredRows}
              onView={openView}
              onEdit={openEdit}
              onDelete={removeRow}
              onAction={runAction}
              onTest={handleTestConfig}
              testingConfigId={testingConfigId}
            />
          )}
        </>
      )}
      {modal && (
        <FormModal
          modal={modal}
          draft={draft}
          setDraft={setDraft}
          error={modalError}
          onClose={() => setModal(null)}
          onSave={saveModal}
        />
      )}
    </section>
  )
}

function AdminAuthError({ error }) {
  const needsLogin = error.status === 401
  return (
    <div className="empty-state">
      <h3>{needsLogin ? '请先在开始页面登录管理员账号' : '当前统一账号没有管理员权限'}</h3>
      <p>{error.message}</p>
      <a className="primary link-button" href="/soup/">去海龟汤大厅登录</a>
    </div>
  )
}

function formatApiError(error) {
  const detail = error?.message
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join('；')
  }
  return detail || '保存失败，请稍后重试'
}

function serializeDraft(tab, draft) {
  const payload = {}
  for (const field of formFields[tab] || []) {
    if (field.key === 'key') continue
    const value = draft[field.key]
    if (field.type === 'checkbox') payload[field.key] = value ? 1 : 0
    else if (field.type === 'number') payload[field.key] = value === '' ? null : Number(value)
    else payload[field.key] = value ?? ''
  }
  if (tab === 'settings') payload.key = draft.key
  return payload
}

function Overview({ data, onOpen }) {
  if (!data) return <p className="loading">加载中...</p>
  return (
    <div className="stats">
      {Object.entries(data).map(([key, count]) => {
        const tab = key === 'puzzle_submissions' ? 'submissions' : key === 'flagged_content' ? 'flags' : key
        return (
          <button className="stat-tile" key={key} onClick={() => tabOrder.includes(tab) && onOpen(tab)}>
            <span>{tableNames[key] || key}</span>
            <b>{count}</b>
          </button>
        )
      })}
    </div>
  )
}

function AdminTable({ tab, rows, onView, onEdit, onDelete, onAction, onTest, testingConfigId }) {
  if (!Array.isArray(rows)) return <p className="loading">加载中...</p>
  if (rows.length === 0) return <p className="loading">暂无记录</p>
  const cols = columns[tab] || Object.keys(rows[0])
  return (
    <div className="admin-table-wrap">
      <table className="admin-data-table">
        <thead>
          <tr>
            {cols.map((key) => <th key={key}>{fieldLabels[key] || key}</th>)}
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(tab, row)}>
              {cols.map((key) => {
                const value = displayValue(key, cellValue(tab, row, key))
                return <td key={key} data-label={fieldLabels[key] || key} title={value}>{clip(value)}</td>
              })}
              <td data-label="操作">
                <div className="row-actions">
                  <button onClick={() => onView(row)}>查看</button>
                  {canEdit.has(tab) && <button onClick={() => onEdit(row)}>编辑</button>}
                  {extraActions(tab, row, onAction, onTest, testingConfigId)}
                  {canDelete.has(tab) && <button className="danger" onClick={() => onDelete(row)}>删除</button>}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function extraActions(tab, row, onAction, onTest, testingConfigId) {
  if (tab === 'api-configs') {
    return (
      <button
        type="button"
        disabled={testingConfigId === row.id}
        onClick={() => onTest(row)}
      >
        {testingConfigId === row.id ? '测试中…' : '测试'}
      </button>
    )
  }
  if (tab === 'puzzles') return <button onClick={() => onAction('togglePuzzle', row)}>{row.enabled ? '停用' : '启用'}</button>
  if (tab === 'submissions') return <><button onClick={() => onAction('addSubmission', row)}>收录</button><button onClick={() => onAction('ignoreSubmission', row)}>忽略</button></>
  if (tab === 'players') return <><button onClick={() => onAction('resetPlayer', row)}>清零</button><button onClick={() => onAction('toggleAdmin', row)}>{row.is_admin ? '取消管理员' : '设为管理员'}</button></>
  if (tab === 'rooms') return <button onClick={() => onAction('finishRoom', row)}>结束</button>
  if (tab === 'reports') return <button onClick={() => onAction('resolveReport', row)}>处理</button>
  if (tab === 'flags') return <button onClick={() => onAction('resolveFlag', row)}>处理</button>
  return null
}

function FormModal({ modal, draft, setDraft, error, onClose, onSave }) {
  const fields = modal.mode === 'view'
    ? Object.keys(draft).map((key) => ({ key, label: fieldLabels[key] || key, readOnly: true }))
    : formFields[modal.tab] || []
  const [testing, setTesting] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [modelOptions, setModelOptions] = useState([])
  const [modalTestResult, setModalTestResult] = useState(null)
  const canTest = modal.tab === 'api-configs' && modal.mode === 'edit' && draft.id
  const canFetchModels = modal.tab === 'api-configs' && modal.mode !== 'view'

  const handleTestConfig = async () => {
    if (!draft.id) {
      setModalTestResult({
        success: false,
        title: '无法测试',
        body: '请先保存配置后再测试',
      })
      return
    }
    setTesting(true)
    setModalTestResult(null)
    try {
      const result = await runApiConfigTest(draft.id, {
        config_id: draft.id || null,
        name: draft.name || '',
        api_url: draft.api_url || '',
        api_key: draft.api_key || '',
        model: draft.model || '',
      })
      setModalTestResult(result)
    } catch (e) {
      setModalTestResult({
        success: false,
        title: '网络错误',
        body: e.message || '请检查接口地址、密钥或模型',
      })
    } finally {
      setTesting(false)
    }
  }

  const handleFetchModels = async () => {
    setFetchingModels(true)
    setModalTestResult(null)
    try {
      const result = await runApiModelFetch({
        config_id: draft.id || null,
        name: draft.name || '',
        api_url: draft.api_url || '',
        api_key: draft.api_key || '',
        model: draft.model || '',
      })
      setModalTestResult(result)
      setModelOptions(result.models || [])
      if (result.success && !draft.model && result.models?.length) {
        setDraft((old) => ({ ...old, model: result.models[0] }))
      }
    } catch (e) {
      setModalTestResult({
        success: false,
        title: '网络错误',
        body: e.message || '请检查接口地址和密钥',
      })
    } finally {
      setFetchingModels(false)
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="admin-modal">
        <div className="modal-head">
          <h3>{modal.title}</h3>
          <button onClick={onClose}>关闭</button>
        </div>
        <div className="admin-form">
          {fields.map((field) => (
            <label className="admin-field" key={field.key}>
              <span>{field.label}</span>
              <FieldInput field={field} draft={draft} setDraft={setDraft} mode={modal.mode} modelOptions={modelOptions} />
            </label>
          ))}
        </div>
        {modalTestResult && (
          <TestResultBlock
            result={modalTestResult}
            onClose={() => setModalTestResult(null)}
            className="cfg-test-result--modal"
          />
        )}
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          {canFetchModels && (
            <button type="button" className="btn-test-leading" onClick={handleFetchModels} disabled={testing || fetchingModels}>
              {fetchingModels ? '拉取中…' : '拉取模型'}
            </button>
          )}
          {canTest && (
            <button type="button" onClick={handleTestConfig} disabled={testing || fetchingModels}>
              {testing ? '测试中…' : '测试'}
            </button>
          )}
          {modal.mode !== 'view' && <button className="primary" onClick={onSave} disabled={testing || fetchingModels}>保存</button>}
          <button onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  )
}

function FieldInput({ field, draft, setDraft, mode, modelOptions = [] }) {
  const readOnly = field.readOnly || (mode === 'edit' && field.readOnlyOnEdit)
  const value = draft[field.key] ?? ''
  if (mode === 'view') return <output>{displayValue(field.key, value)}</output>
  if (field.type === 'checkbox') {
    return <input type="checkbox" checked={Boolean(value)} onChange={(e) => setDraft((old) => ({ ...old, [field.key]: e.target.checked ? 1 : 0 }))} />
  }
  if (field.type === 'select') {
    return (
      <select value={value} onChange={(e) => setDraft((old) => ({ ...old, [field.key]: e.target.value }))}>
        {field.options.map((option) => <option key={option} value={option}>{statusText[option] || option}</option>)}
      </select>
    )
  }
  if (field.type === 'textarea') {
    return <textarea required={field.required} readOnly={readOnly} value={value} onChange={(e) => setDraft((old) => ({ ...old, [field.key]: e.target.value }))} />
  }
  if (field.key === 'model' && modelOptions.length > 0) {
    return (
      <>
        <input
          list="api-model-options"
          required={field.required}
          readOnly={readOnly}
          value={value}
          onChange={(e) => setDraft((old) => ({ ...old, [field.key]: e.target.value }))}
        />
        <datalist id="api-model-options">
          {modelOptions.map((model) => <option key={model} value={model} />)}
        </datalist>
      </>
    )
  }
  return (
    <input
      type={field.type || 'text'}
      required={field.required}
      readOnly={readOnly}
      placeholder={field.secretOnEdit && mode === 'edit' ? '留空则保留原密钥' : ''}
      value={value}
      onChange={(e) => setDraft((old) => ({ ...old, [field.key]: e.target.value }))}
    />
  )
}
