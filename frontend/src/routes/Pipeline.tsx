import { ApprovalGate } from '../components/ApprovalGate'
import { ChannelReviewQueuePanel } from '../components/ChannelReviewQueuePanel'
import { PipelineBody } from '../components/ContentPipeline'
import { updatePipelineApproval } from '../lib/api'
import { usePipelineStore } from '../stores/pipelineStore'
import { useToastStore } from '../stores/toastStore'

export default function PipelineRoute() {
  const snapshot = usePipelineStore((state) => state.snapshot)
  const currentJob = usePipelineStore((state) => state.currentJob)
  const setSnapshot = usePipelineStore((state) => state.setSnapshot)
  const setAwaitingApproval = usePipelineStore((state) => state.setAwaitingApproval)
  const pushToast = useToastStore((state) => state.push)

  return (
    <div className="shell-route scroll-chrome h-full overflow-y-auto px-4 py-6">
      <div className="mx-auto flex max-w-[1480px] flex-col gap-6">
        <section className="shell-hero p-6">
          <div className="label-eyebrow">PIPELINE</div>
          <h1 className="mt-2 type-h1 text-text-primary">Centrum wykonania treści</h1>
          <p className="mt-4 max-w-3xl type-body text-text-secondary">
            Ten widok pokazuje realny stan kroków, blokad approval, gotowych shortów i
            paczek wymagających naprawy przed uploadem.
          </p>
        </section>

        <ChannelReviewQueuePanel />

        <section className="shell-panel-strong overflow-hidden">
          <PipelineBody snapshot={snapshot} />
        </section>
      </div>
      <ApprovalGate
        snapshot={snapshot}
        onApprove={async (workItemId) => {
          const activeJobId = currentJob?.id
          if (activeJobId) {
            setAwaitingApproval(activeJobId, false)
          }

          try {
            const next = await updatePipelineApproval(workItemId, { action: 'approve_review' })
            setSnapshot(next)
            pushToast({
              type: 'success',
              title: 'Preview zatwierdzony',
              message: 'Pakiet publikacji został oznaczony jako approved.',
            })
          } catch (error) {
            if (activeJobId) {
              setAwaitingApproval(activeJobId, true)
            }
            pushToast({
              type: 'error',
              title: 'Nie udało się zatwierdzić publikacji',
              message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
            })
            throw error
          }
        }}
        onReject={async (workItemId) => {
          const activeJobId = currentJob?.id
          if (activeJobId) {
            setAwaitingApproval(activeJobId, false)
          }

          try {
            const next = await updatePipelineApproval(workItemId, { action: 'request_changes' })
            setSnapshot(next)
            pushToast({
              type: 'warning',
              title: 'Preview cofnięty',
              message: 'Pakiet wrócił do statusu wymagającego zmian.',
            })
          } catch (error) {
            if (activeJobId) {
              setAwaitingApproval(activeJobId, true)
            }
            pushToast({
              type: 'error',
              title: 'Nie udało się odrzucić publikacji',
              message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
            })
            throw error
          }
        }}
      />
    </div>
  )
}
