import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, del, post, put } from '../api'
import ColoredSurface from '../components/ColoredSurface.jsx'
import TagInput, { joinTags, parseTags } from '../components/TagInput.jsx'

const emptyForm = { title: '', surface: '', answer: '', tagList: [] }

export default function AddPuzzle() {
  const [form, setForm] = useState(emptyForm)
  const [editingId, setEditingId] = useState(null)
  const [puzzles, setPuzzles] = useState([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadPuzzles = async () => {
    setLoading(true)
    try {
      setPuzzles(await api('/puzzles/'))
      setError('')
    } catch (e) {
      setPuzzles([])
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadPuzzles() }, [])

  const resetForm = () => {
    setForm(emptyForm)
    setEditingId(null)
    setMessage('')
    setError('')
  }

  const startEdit = async (id) => {
    setMessage('')
    setError('')
    try {
      const row = await api(`/puzzles/${id}`)
      setForm({
        title: row.title || '',
        surface: row.surface || '',
        answer: row.answer || '',
        tagList: parseTags(row.tags || ''),
      })
      setEditingId(id)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setError(e.message)
    }
  }

  const submit = async (event) => {
    event.preventDefault()
    setMessage('')
    setError('')
    const surface = form.surface.trim()
    const answer = form.answer.trim()
    if (!surface || !answer) {
      setError('汤面和汤底必填')
      return
    }
    const payload = {
      title: form.title.trim(),
      surface,
      answer,
      tags: joinTags(form.tagList),
    }
    setSubmitting(true)
    try {
      if (editingId) {
        await put(`/puzzles/${editingId}`, payload)
        setMessage(`已更新题目 #${editingId}`)
      } else {
        const data = await post('/puzzles/', payload)
        setMessage(`已入库，题目 id：${data.id}`)
      }
      resetForm()
      await loadPuzzles()
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  const remove = async (id, title) => {
    const label = title || `#${id}`
    if (!window.confirm(`确定删除「${label}」？此操作不可恢复。`)) return
    setMessage('')
    setError('')
    try {
      await del(`/puzzles/${id}`)
      if (editingId === id) resetForm()
      setMessage(`已删除 #${id}`)
      await loadPuzzles()
    } catch (e) {
      setError(e.message)
    }
  }

  const toggleEnabled = async (id) => {
    try {
      await api(`/puzzles/${id}/toggle`, { method: 'PATCH' })
      await loadPuzzles()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <section className="add-puzzle-page">
      <div className="section-head">
        <div>
          <h2>临时加题</h2>
          <p className="muted">直接写入海龟汤题库（需管理员登录）；汤面可用 [[红|文字]] 标颜色；线索汤请用【线索公布】和【线索公布结束】包住中途公开内容。</p>
        </div>
        <Link to="/admin">管理后台</Link>
      </div>

      <form className="panel add-puzzle-form" onSubmit={submit}>
        <h3>{editingId ? `编辑题目 #${puzzles.findIndex((p) => p.id === editingId) + 1}` : '新增题目'}</h3>
        <label>
          汤名（可选）
          <input
            placeholder="短标题，便于在列表中识别"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </label>
        <label>
          汤面
          <textarea
            placeholder="汤面；彩色文字格式：[[红|要标红的内容]]"
            value={form.surface}
            onChange={(e) => setForm({ ...form, surface: e.target.value })}
            required
          />
        </label>
        <label>
          汤底
          <textarea
            placeholder={'汤底\n\n线索汤格式：\n【线索公布】\n这里写中途要公开的线索\n【线索公布结束】\n\n后面继续写完整汤底和通关条件'}
            value={form.answer}
            onChange={(e) => setForm({ ...form, answer: e.target.value })}
            required
          />
        </label>
        <label>
          标签（可选）
          <TagInput
            tags={form.tagList}
            onChange={(tagList) => setForm({ ...form, tagList })}
            placeholder="输入后按 Enter 或逗号添加标签"
          />
        </label>
        <div className="actions">
          <button type="submit" className="primary" disabled={submitting}>
            {submitting ? '提交中…' : editingId ? '保存修改' : '加入题库'}
          </button>
          {editingId ? (
            <button type="button" onClick={resetForm} disabled={submitting}>取消编辑</button>
          ) : (
            <button type="button" onClick={resetForm} disabled={submitting}>清空</button>
          )}
        </div>
        {message && <p className="success">{message}</p>}
        {error && <p className="error">{error}</p>}
      </form>

      <section className="panel">
        <div className="section-head">
          <h3>题库列表</h3>
          <button type="button" onClick={loadPuzzles} disabled={loading}>刷新</button>
        </div>
        {loading ? (
          <p className="muted">加载中…</p>
        ) : puzzles.length === 0 ? (
          <p className="muted">暂无题目或无权查看。</p>
        ) : (
          <ol className="puzzle-bank-list">
            {puzzles.map((item, i) => (
              <li key={item.id}>
                <div className="puzzle-bank-head">
                  <span className="puzzle-id">#{i + 1}</span>
                  <span className={item.enabled ? 'enabled' : 'disabled'}>
                    {item.enabled ? '启用' : '禁用'}
                  </span>
                  <div className="puzzle-bank-actions">
                    <button type="button" onClick={() => startEdit(item.id)}>修改</button>
                    <button type="button" onClick={() => toggleEnabled(item.id)}>
                      {item.enabled ? '禁用' : '启用'}
                    </button>
                    <button type="button" onClick={() => remove(item.id, item.title)}>删除</button>
                  </div>
                </div>
                {item.title ? <h4 className="puzzle-title">{item.title}</h4> : null}
                <ColoredSurface text={item.surface} />
                {item.tags ? (
                  <div className="tag-chip-row">
                    {parseTags(item.tags).map((tag) => (
                      <span className="tag-chip tag-chip-readonly" key={`${item.id}-${tag}`}>{tag}</span>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  )
}
