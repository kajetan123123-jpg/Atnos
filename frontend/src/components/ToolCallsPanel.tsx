import type { AgentLogEntry, AgentRecentLogsPayload } from '../types/system'

interface ToolCallsPanelProps {
  logs: AgentRecentLogsPayload | null
}

function formatLogTitle(item: AgentLogEntry) {
  return item.toolName ?? item.event ?? item.step ?? 'log entry'
}

function formatLogDetail(item: AgentLogEntry) {
  return item.error ?? item.message ?? item.command ?? item.status ?? 'ok'
}

function LogList({ title, items }: { title: string; items: AgentLogEntry[] }) {
  return (
    <div className="shell-bento rounded-lg p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="type-label text-text-tertiary">{title}</div>
        <div className="type-mono-sm text-text-tertiary">{items.length}</div>
      </div>
      <div className="space-y-2">
        {items.length ? (
          items.slice(0, 8).map((item, index) => (
            <div
              key={`${item.timestamp ?? 'no-time'}-${index}`}
              className="rounded-md border border-border-default bg-elevated/60 px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 type-body-sm text-text-primary">{formatLogTitle(item)}</div>
                <div className="shrink-0 type-mono-sm text-text-tertiary">{item.timestamp ? 'logged' : 'raw'}</div>
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-text-secondary">{formatLogDetail(item)}</div>
            </div>
          ))
        ) : (
          <div className="type-body-sm text-text-tertiary">Brak ostatnich wpisow.</div>
        )}
      </div>
    </div>
  )
}

export function ToolCallsPanel({ logs }: ToolCallsPanelProps) {
  return (
    <section className="grid gap-4 xl:grid-cols-2" data-testid="tool-calls-panel">
      <LogList title="OSTATNIE DECYZJE" items={logs?.decisions ?? []} />
      <LogList title="OSTATNIE TOOL CALLE" items={logs?.toolCalls ?? []} />
    </section>
  )
}
