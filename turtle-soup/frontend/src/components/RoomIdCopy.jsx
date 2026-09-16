import { useEffect, useRef, useState } from 'react'

function plainRoomId(roomId) {
  return String(roomId ?? '').trim().replace(/^#\s*/, '').trim()
}

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Fall through for browsers that expose Clipboard API but deny access.
    }
  }

  const input = document.createElement('textarea')
  input.value = text
  input.setAttribute('readonly', '')
  input.style.position = 'fixed'
  input.style.opacity = '0'
  document.body.appendChild(input)
  let copied = false
  try {
    input.select()
    copied = document.execCommand('copy')
  } finally {
    input.remove()
  }
  if (!copied) throw new Error('copy failed')
}

export default function RoomIdCopy({ roomId, className = '' }) {
  const [feedback, setFeedback] = useState('')
  const timerRef = useRef(null)
  const id = plainRoomId(roomId)

  useEffect(() => () => clearTimeout(timerRef.current), [])

  const copy = async (event) => {
    event.preventDefault()
    event.stopPropagation()
    try {
      await writeClipboard(id)
      setFeedback('已复制')
    } catch {
      setFeedback('复制失败')
    }
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setFeedback(''), 1400)
  }

  return (
    <span className={`room-id-copy-wrap ${className}`.trim()}>
      <button
        type="button"
        className="room-id-copy"
        onClick={copy}
        aria-label={`复制房间 ID ${id}`}
        title={`复制房间 ID ${id}`}
      >
        <span aria-live="polite">{feedback || `房间 #${id}`}</span>
      </button>
    </span>
  )
}
