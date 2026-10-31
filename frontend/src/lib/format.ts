const timeFormatter = new Intl.DateTimeFormat('pl-PL', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

const shortDateTimeFormatter = new Intl.DateTimeFormat('pl-PL', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const relativeTimeFormatter = new Intl.RelativeTimeFormat('pl-PL', {
  numeric: 'auto',
})

const numberFormatter = new Intl.NumberFormat('pl-PL', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

export function formatClock(value?: string | null) {
  if (!value) return '--:--:--'
  return timeFormatter.format(new Date(value))
}

export function formatDateTime(value?: string | null) {
  if (!value) return '—'
  return shortDateTimeFormatter.format(new Date(value))
}

export function formatRelativeDateTime(value?: string | null) {
  if (!value) return '—'
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) {
    return formatDateTime(value)
  }

  const deltaMs = timestamp - Date.now()
  const deltaMinutes = Math.round(deltaMs / 60000)
  if (Math.abs(deltaMinutes) < 60) {
    return relativeTimeFormatter.format(deltaMinutes, 'minute')
  }

  const deltaHours = Math.round(deltaMinutes / 60)
  if (Math.abs(deltaHours) < 24) {
    return relativeTimeFormatter.format(deltaHours, 'hour')
  }

  const deltaDays = Math.round(deltaHours / 24)
  if (Math.abs(deltaDays) < 7) {
    return relativeTimeFormatter.format(deltaDays, 'day')
  }

  return formatDateTime(value)
}

export function formatVram(usedMB: number, totalMB: number) {
  return `${numberFormatter.format(usedMB / 1024)} / ${numberFormatter.format(totalMB / 1024)} GB`
}

export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`
}

export function formatMinutes(valueMs: number | null) {
  if (valueMs == null) return '—'
  const totalSeconds = Math.max(0, Math.round(valueMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
}
