import { useRef } from 'react'

export function parseTags(value) {
  if (!value) return []
  return [...new Set(
    String(value)
      .split(/[,，、;；\s]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  )]
}

export function joinTags(tags) {
  return tags.join('，')
}

export default function TagInput({ tags, onChange, placeholder = '输入后按 Enter 或逗号添加' }) {
  const inputRef = useRef(null)

  const addTag = (raw) => {
    const text = raw.trim()
    if (!text) return
    if (tags.includes(text)) return
    onChange([...tags, text])
  }

  const commitDraft = () => {
    const input = inputRef.current
    if (!input) return
    addTag(input.value)
    input.value = ''
  }

  const onKeyDown = (event) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      commitDraft()
      return
    }
    if (event.key === 'Backspace' && !event.currentTarget.value && tags.length) {
      onChange(tags.slice(0, -1))
    }
  }

  const onBlur = () => {
    commitDraft()
  }

  return (
    <div className="tag-input" onClick={() => inputRef.current?.focus()}>
      {tags.map((tag) => (
        <span className="tag-chip" key={tag}>
          {tag}
          <button
            type="button"
            className="tag-chip-remove"
            aria-label={`移除标签 ${tag}`}
            onClick={(event) => {
              event.stopPropagation()
              onChange(tags.filter((item) => item !== tag))
            }}
          >
            ×
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        className="tag-input-field"
        type="text"
        placeholder={tags.length ? '' : placeholder}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
    </div>
  )
}
