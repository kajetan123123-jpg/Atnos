import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  updateProject,
  type ProjectPersonaProfile,
  type ProjectProviderPosture,
} from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { useToastStore } from '../stores/toastStore'

const PERSONA_OPTIONS: Array<{ value: ProjectPersonaProfile; label: string }> = [
  { value: 'strategist', label: 'Strateg' },
  { value: 'critic', label: 'Krytyk' },
  { value: 'executor', label: 'Wykonawca' },
  { value: 'mentor', label: 'Mentor' },
  { value: 'jarvis-mode', label: 'Jarvis' },
]

const POSTURE_OPTIONS: Array<{ value: ProjectProviderPosture; label: string }> = [
  { value: 'strict-local', label: 'Tylko lokalnie' },
  { value: 'prefer-local', label: 'Preferuj lokalne' },
  { value: 'ask-before-external', label: 'Pytaj przed chmurą' },
  { value: 'improve-when-needed', label: 'Chmura gdy trzeba' },
]

type Props = {
  selectedProjectId: number | null
  onSelectProject: (projectId: number) => void
}

export function SettingsProjectsPanel({ selectedProjectId, onSelectProject }: Props) {
  const projects = useChatStore((state) => state.projects)
  const pushToast = useToastStore((state) => state.push)
  const selected = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  )
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [persona, setPersona] = useState<ProjectPersonaProfile>('strategist')
  const [posture, setPosture] = useState<ProjectProviderPosture>('prefer-local')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!selected) {
      setName('')
      setDescription('')
      setPersona('strategist')
      setPosture('prefer-local')
      return
    }
    setName(selected.name || '')
    setDescription(selected.description || '')
    setPersona((selected.personaProfile as ProjectPersonaProfile) || 'strategist')
    setPosture((selected.providerPosture as ProjectProviderPosture) || 'prefer-local')
  }, [selected])

  if (projects.length === 0) {
    return (
      <div className="border border-border-default bg-void p-4 text-sm text-text-secondary">
        Brak projektów. Utwórz projekt w centrum projektów albo z czatu.
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="settings-projects-panel">
      <div className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Projekty</div>
        <p className="mt-2 text-sm text-text-secondary">
          Ustawienia per projekt (persona, postura, opis). Produkcja kanałów i konta contentowe są
          poniżej w sekcji projektu.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              onClick={() => onSelectProject(project.id)}
              className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                selectedProjectId === project.id
                  ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary'
              }`}
            >
              {project.name}
            </button>
          ))}
        </div>
      </div>

      {selected ? (
        <div className="border border-border-default bg-panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="label-eyebrow">Aktywny projekt</div>
              <div className="mt-1 text-lg text-text-primary">{selected.name}</div>
            </div>
            <Link
              to={`/projects/${selected.id}`}
              className="border border-border-default px-3 py-1.5 text-[11px] uppercase tracking-[0.12em] text-text-secondary"
            >
              Otwórz panel projektu
            </Link>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <label className="block">
              <span className="label-eyebrow">Nazwa</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm"
              />
            </label>
            <label className="block lg:col-span-2">
              <span className="label-eyebrow">Opis</span>
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
                className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm"
              />
            </label>
          </div>

          <div className="mt-4">
            <div className="label-eyebrow">Persona projektu</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {PERSONA_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setPersona(option.value)}
                  className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                    persona === option.value
                      ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                      : 'border-border-default text-text-secondary'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4">
            <div className="label-eyebrow">Postura providerów projektu</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {POSTURE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setPosture(option.value)}
                  className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                    posture === option.value
                      ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                      : 'border-border-default text-text-secondary'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 flex justify-end">
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                void (async () => {
                  setSaving(true)
                  try {
                    await updateProject(selected.id, {
                      name,
                      description,
                      personaProfile: persona,
                      providerPosture: posture,
                    })
                    useChatStore.setState((state) => ({
                      projects: state.projects.map((project) =>
                        project.id === selected.id
                          ? {
                              ...project,
                              name,
                              description: description || null,
                              personaProfile: persona,
                              providerPosture: posture,
                            }
                          : project,
                      ),
                    }))
                    pushToast({
                      type: 'success',
                      title: 'Projekt zapisany',
                      message: selected.name,
                    })
                  } catch (error) {
                    pushToast({
                      type: 'error',
                      title: 'Zapis projektu nieudany',
                      message: error instanceof Error ? error.message : 'Nieznany błąd',
                    })
                  } finally {
                    setSaving(false)
                  }
                })()
              }}
              className="border border-accent-primary bg-accent-primary/10 px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:opacity-60"
            >
              {saving ? 'Zapisuję…' : 'Zapisz projekt'}
            </button>
          </div>
        </div>
      ) : (
        <div className="border border-border-default bg-void p-4 text-sm text-text-secondary">
          Wybierz projekt powyżej.
        </div>
      )}
    </div>
  )
}
