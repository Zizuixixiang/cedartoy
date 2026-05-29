import { useState } from 'react'
import { del, post, put } from '../api'

export default function NoteBoard({ roomId, notes, setNotes }) {
  const [content, setContent] = useState('')
  const add = async () => {
    if (!content.trim()) return
    const note = await post(`/notes/${roomId}`, { content })
    setNotes((items) => [note, ...items])
    setContent('')
  }
  const remove = async (id) => {
    await del(`/notes/${id}`)
    setNotes((items) => items.filter((n) => n.id !== id))
  }
  const edit = async (note) => {
    const next = prompt('修改记事', note.content)
    if (!next) return
    const updated = await put(`/notes/${note.id}`, { content: next })
    setNotes((items) => items.map((n) => (n.id === note.id ? updated : n)))
  }
  return (
    <aside className="notes">
      <h3>记事板</h3>
      <div className="note-input"><input maxLength="50" value={content} onChange={(e) => setContent(e.target.value)} /><button onClick={add}>添加</button></div>
      {notes.map((note) => <div className="note" key={note.id}><p>{note.content}</p><small>{note.username || `游客${note.player_id}`}</small><div><button onClick={() => edit(note)}>改</button><button onClick={() => remove(note.id)}>删</button></div></div>)}
    </aside>
  )
}
