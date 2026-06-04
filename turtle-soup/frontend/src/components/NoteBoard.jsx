import { useState } from 'react'
import { del, post, put } from '../api'
import { noteAuthor } from '../utils/display.js'

function prependNoteOnce(items, note) {
  return items.some((item) => Number(item.id) === Number(note.id)) ? items : [note, ...items]
}

export default function NoteBoard({ roomId, notes, setNotes }) {
  const [content, setContent] = useState('')

  const addNote = async () => {
    const text = content.trim()
    if (!text) return
    const note = await post(`/notes/${roomId}`, { content: text })
    setNotes((items) => prependNoteOnce(items, note))
    setContent('')
  }

  const remove = async (id) => {
    await del(`/notes/${id}`)
    setNotes((items) => items.filter((note) => note.id !== id))
  }

  const edit = async (note) => {
    const next = prompt('修改笔记', note.content)
    if (!next?.trim()) return
    const updated = await put(`/notes/${note.id}`, { content: next.trim() })
    setNotes((items) => items.map((item) => (item.id === note.id ? updated : item)))
  }

  return (
    <aside className="room-notepad">
      <div className="terminal-head">
        <span className="lights"><i /><i /><i /></span>
        <strong>记事板</strong>
        <small>共享</small>
      </div>
      <div className="notepad-body">
        {notes.length > 0 ? (
          <div className="notepad-saved">
            {notes.map((note) => (
              <div className="notepad-item" key={note.id}>
                <p>{note.content}</p>
                <footer>
                  <small>{noteAuthor(note)}</small>
                  <div>
                    <button type="button" onClick={() => edit(note)}>改</button>
                    <button type="button" onClick={() => remove(note.id)}>删</button>
                  </div>
                </footer>
              </div>
            ))}
          </div>
        ) : (
          <p className="notepad-empty">暂无笔记，添加后全房间可见。</p>
        )}
      </div>
      <div className="notepad-actions">
        <input
          className="notepad-input"
          maxLength={50}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              addNote()
            }
          }}
          placeholder="记录推理线索…"
          aria-label="笔记内容"
        />
        <button type="button" className="pixel-primary" disabled={!content.trim()} onClick={addNote}>
          添加
        </button>
      </div>
    </aside>
  )
}
