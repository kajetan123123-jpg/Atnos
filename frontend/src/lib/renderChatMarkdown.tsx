import type { ReactNode } from 'react'

/**
 * Lekki renderer markdownu czatu bez nowych zależności:
 * akapity, nagłówki, listy, linki, **bold**, *italic*, `inline code`.
 * Bloki ``` obsługuje ChatMessage osobno (Shiki).
 */

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern =
    /(\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\n]+)\*|_([^_\n]+)_|https?:\/\/[^\s<]+)/g
  let last = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index))
    }
    const key = `${keyPrefix}-${index++}`
    if (match[2] && match[3]) {
      const href = match[3]
      const external = href.startsWith('http')
      nodes.push(
        <a
          key={key}
          href={href}
          className="text-accent-primary underline underline-offset-2 hover:brightness-110"
          {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}
        >
          {match[2]}
        </a>,
      )
    } else if (match[4]) {
      nodes.push(
        <code
          key={key}
          className="rounded-sm border border-border-subtle bg-elevated px-1 py-0.5 font-mono text-[0.9em] text-text-primary"
        >
          {match[4]}
        </code>,
      )
    } else if (match[5]) {
      nodes.push(
        <strong key={key} className="font-semibold text-text-primary">
          {match[5]}
        </strong>,
      )
    } else if (match[6] || match[7]) {
      nodes.push(
        <em key={key} className="italic text-text-primary">
          {match[6] || match[7]}
        </em>,
      )
    } else if (match[1] || match[0]) {
      const url = match[0]
      nodes.push(
        <a
          key={key}
          href={url}
          target="_blank"
          rel="noreferrer"
          className="break-all text-accent-primary underline underline-offset-2 hover:brightness-110"
        >
          {url}
        </a>,
      )
    }
    last = match.index + match[0].length
  }
  if (last < text.length) {
    nodes.push(text.slice(last))
  }
  return nodes
}

function isUnorderedItem(line: string) {
  return /^[-*•]\s+/.test(line)
}

function isOrderedItem(line: string) {
  return /^\d+[.)]\s+/.test(line)
}

function headingLevel(line: string): 1 | 2 | 3 | null {
  if (/^###\s+\S/.test(line)) return 3
  if (/^##\s+\S/.test(line)) return 2
  if (/^#\s+\S/.test(line)) return 1
  return null
}

export function ChatMarkdownBody({ text }: { text: string }) {
  const normalized = text.replace(/\r\n/g, '\n').trim()
  if (!normalized) {
    return null
  }

  const lines = normalized.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let blockIndex = 0

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i += 1
      continue
    }

    const heading = headingLevel(line)
    if (heading) {
      const content = line.replace(/^#{1,3}\s+/, '')
      const className =
        heading === 1
          ? 'type-h3 font-semibold text-text-primary'
          : heading === 2
            ? 'type-body font-semibold text-text-primary'
            : 'type-body-sm font-semibold uppercase tracking-[0.04em] text-text-secondary'
      const Tag = heading === 1 ? 'h3' : heading === 2 ? 'h4' : 'h5'
      blocks.push(
        <Tag key={`h-${blockIndex++}`} className={className}>
          {renderInline(content, `h-${blockIndex}`)}
        </Tag>,
      )
      i += 1
      continue
    }

    if (isUnorderedItem(line) || isOrderedItem(line)) {
      const ordered = isOrderedItem(line)
      const items: string[] = []
      while (i < lines.length && (ordered ? isOrderedItem(lines[i]) : isUnorderedItem(lines[i]))) {
        items.push(lines[i].replace(ordered ? /^\d+[.)]\s+/ : /^[-*•]\s+/, ''))
        i += 1
      }
      const ListTag = ordered ? 'ol' : 'ul'
      blocks.push(
        <ListTag
          key={`list-${blockIndex++}`}
          className={
            ordered
              ? 'list-decimal space-y-1 pl-5 type-body text-text-primary'
              : 'list-disc space-y-1 pl-5 type-body text-text-primary'
          }
        >
          {items.map((item, itemIndex) => (
            <li key={`li-${blockIndex}-${itemIndex}`} className="leading-6">
              {renderInline(item, `li-${blockIndex}-${itemIndex}`)}
            </li>
          ))}
        </ListTag>,
      )
      continue
    }

    const paragraphLines: string[] = [line]
    i += 1
    while (
      i < lines.length &&
      lines[i].trim() &&
      !isUnorderedItem(lines[i]) &&
      !isOrderedItem(lines[i]) &&
      !headingLevel(lines[i])
    ) {
      paragraphLines.push(lines[i])
      i += 1
    }
    const paragraph = paragraphLines.join('\n')
    blocks.push(
      <p
        key={`p-${blockIndex++}`}
        className="whitespace-pre-wrap type-body leading-6 text-text-primary"
      >
        {renderInline(paragraph, `p-${blockIndex}`)}
      </p>,
    )
  }

  return <div className="space-y-2.5 break-words">{blocks}</div>
}
