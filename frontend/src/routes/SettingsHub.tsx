import { useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SettingsGeneralChatPanel } from '../components/SettingsGeneralChatPanel'
import { SettingsGeneralProvidersPanel } from '../components/SettingsGeneralProvidersPanel'
import { SettingsProjectsPanel } from '../components/SettingsProjectsPanel'
import ExternalReposPanel from '../components/ExternalReposPanel'
import PublicDataIntegrationsPanel from '../components/PublicDataIntegrationsPanel'
import ContentMachineSettingsRoute from './ContentMachineSettings'
import { useChatStore } from '../stores/chatStore'

type Area = 'general' | 'projects'
type GeneralTab = 'chat' | 'models' | 'production-runtime' | 'integrations'

const AREA_OPTIONS: Array<{ id: Area; label: string; hint: string }> = [
  {
    id: 'general',
    label: 'Ogólne',
    hint: 'Czat, modele API i globalny runtime Atnosa',
  },
  {
    id: 'projects',
    label: 'Projekty',
    hint: 'Ustawienia konkretnego projektu oraz produkcja kanałów',
  },
]

const GENERAL_TABS: Array<{ id: GeneralTab; label: string }> = [
  { id: 'chat', label: 'Czat' },
  { id: 'models', label: 'Modele i API' },
  { id: 'production-runtime', label: 'Runtime produkcji' },
  { id: 'integrations', label: 'Integracje' },
]

function parseArea(value: string | null): Area {
  return value === 'projects' ? 'projects' : 'general'
}

function parseGeneralTab(value: string | null): GeneralTab {
  if (value === 'models' || value === 'production-runtime' || value === 'integrations') {
    return value
  }
  return 'chat'
}

export default function SettingsHubRoute() {
  const [searchParams, setSearchParams] = useSearchParams()
  const projects = useChatStore((state) => state.projects)
  const selectedProjectId = useChatStore((state) => state.selectedProjectId)
  const setSelectedProjectId = useChatStore((state) => state.setSelectedProjectId)

  const area = parseArea(searchParams.get('area'))
  const generalTab = parseGeneralTab(searchParams.get('tab'))

  const activeProjectId = useMemo(() => {
    if (selectedProjectId != null && projects.some((project) => project.id === selectedProjectId)) {
      return selectedProjectId
    }
    return projects[0]?.id ?? null
  }, [projects, selectedProjectId])

  useEffect(() => {
    if (area === 'projects' && activeProjectId != null && selectedProjectId !== activeProjectId) {
      setSelectedProjectId(activeProjectId)
    }
  }, [area, activeProjectId, selectedProjectId, setSelectedProjectId])

  const setArea = (next: Area) => {
    const params = new URLSearchParams(searchParams)
    params.set('area', next)
    if (next === 'general' && !params.get('tab')) {
      params.set('tab', 'chat')
    }
    if (next === 'projects') {
      params.delete('tab')
    }
    setSearchParams(params, { replace: true })
  }

  const setGeneralTab = (next: GeneralTab) => {
    const params = new URLSearchParams(searchParams)
    params.set('area', 'general')
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="box-border w-full min-w-0 max-w-[100vw] overflow-x-hidden p-4" data-testid="settings-hub">
      <section className="w-full min-w-0 max-w-full border border-border-default bg-panel">
        <div className="flex min-w-0 max-w-full flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-3">
          <div className="min-w-0 max-w-full">
            <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-tertiary">
              Ustawienia
            </div>
            <div className="mt-1 font-display text-xl font-semibold uppercase tracking-[0.1em] text-text-primary">
              Atnos — czytelny podział
            </div>
            <p className="mt-2 max-w-3xl text-sm text-text-secondary">
              {area === 'general'
                ? 'Ogólne: zarządzanie czatem, modele i globalny runtime.'
                : 'Projekty: persona/postura projektu oraz ustawienia produkcji kanałów.'}
            </p>
          </div>
          <div
            role="tablist"
            aria-label="Obszary ustawień"
            className="flex flex-wrap gap-2"
          >
            {AREA_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="tab"
                title={option.hint}
                aria-selected={area === option.id}
                onClick={() => setArea(option.id)}
                className={[
                  'border px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em]',
                  area === option.id
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary',
                ].join(' ')}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {area === 'general' ? (
          <div className="min-w-0 max-w-full overflow-x-hidden p-4">
            <div role="tablist" aria-label="Sekcje ogólne" className="mb-4 flex flex-wrap gap-2">
              {GENERAL_TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={generalTab === item.id}
                  onClick={() => setGeneralTab(item.id)}
                  className={[
                    'border px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em]',
                    generalTab === item.id
                      ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                      : 'border-border-default text-text-secondary',
                  ].join(' ')}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {generalTab === 'chat' ? <SettingsGeneralChatPanel /> : null}
            {generalTab === 'models' ? <SettingsGeneralProvidersPanel /> : null}
            {generalTab === 'production-runtime' ? (
              <ContentMachineSettingsRoute embedded initialTab="runtime" />
            ) : null}
            {generalTab === 'integrations' ? (
              <>
                <PublicDataIntegrationsPanel />
                <ExternalReposPanel />
              </>
            ) : null}
          </div>
        ) : (
          <div className="min-w-0 max-w-full space-y-4 overflow-x-hidden p-4">
            <SettingsProjectsPanel
              selectedProjectId={activeProjectId}
              onSelectProject={(projectId) => setSelectedProjectId(projectId)}
            />
            <div className="border border-border-default bg-void p-3">
              <div className="label-eyebrow">Produkcja i konta kanałów (projekt)</div>
              <p className="mt-2 text-sm text-text-secondary">
                Pipeline, monetyzacja, automatyzacja i dostępy contentowe dla aktywnego kontekstu
                produkcyjnego.
              </p>
            </div>
            <ContentMachineSettingsRoute embedded />
          </div>
        )}
      </section>
    </div>
  )
}
