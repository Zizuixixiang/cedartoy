import React from 'react'

const COLOR_ALIASES = {
  红: 'red',
  红色: 'red',
  red: 'red',
  橙: 'orange',
  橙色: 'orange',
  orange: 'orange',
  黄: 'yellow',
  黄色: 'yellow',
  yellow: 'yellow',
  绿: 'green',
  绿色: 'green',
  green: 'green',
  蓝: 'blue',
  蓝色: 'blue',
  blue: 'blue',
  紫: 'purple',
  紫色: 'purple',
  purple: 'purple',
  粉: 'pink',
  粉色: 'pink',
  pink: 'pink',
  灰: 'gray',
  灰色: 'gray',
  gray: 'gray',
  grey: 'gray',
  黑: 'black',
  黑色: 'black',
  black: 'black',
  白: 'white',
  白色: 'white',
  white: 'white',
}

const MARKER_RE = /\[\[\s*([^\]|\n]{1,12}?)\s*\|\s*([^\]]{1,200})\]\]/g

function renderColoredText(text) {
  const source = String(text || '')
  const nodes = []
  let lastIndex = 0
  let match

  while ((match = MARKER_RE.exec(source))) {
    const [raw, colorName, content] = match
    const color = COLOR_ALIASES[colorName.trim().toLowerCase()]
    if (!color) continue

    if (match.index > lastIndex) nodes.push(source.slice(lastIndex, match.index))
    nodes.push(
      <span className={`colored-surface-token color-${color}`} key={`${match.index}-${color}`}>
        {content}
      </span>,
    )
    lastIndex = match.index + raw.length
  }

  if (lastIndex < source.length) nodes.push(source.slice(lastIndex))
  return nodes
}

export default function ColoredSurface({ text, as: Component = 'p', className = '' }) {
  return <Component className={className}>{renderColoredText(text)}</Component>
}
