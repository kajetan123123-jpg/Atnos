import { useCallback, useEffect, useState } from 'react'
import {
  fetchSettingsControlCenter,
  updateSettingsPolicy,
  updateSettingsStyle,
} from '../lib/api'
import {
  CHAT_WORK_MODE_OPTIONS,
  readChatWorkMode,
  writeChatWorkMode,
  type ChatWorkMode,
} from '../lib/chatPreferences'
import type { SettingsControlCenterPayload } from '../types/settings'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'

const LENGTH_OPTIONS = [
  { value: 'short', label: 'Krótka' },
  { value: 'medium', label: 'Standard' },
  { value: 'long', label: 'Rozbudowana' },
] as const

const TONE_OPTIONS = [
  { value: 'direct', label: 'Bezpośredni' },
  { value: 'professional', label: 'Profesjonalny' },
  { value: 'natural', label: 'Naturalny' },
  { value: 'supportive', label: 'Wspierający' },
] as const

const PERSONA_OPTIONS = [
  { value: 'strategist', label: 'Strateg' },
  { value: 'critic', label: 'Krytyk' },
  { value: 'executor', label: 'Wykonawca' },
  { value: 'mentor', label: 'Mentor' },
  { value: 'jarvis-mode', label: 'Jarvis' },
] as const

const POSTURE_OPTIONS = [
  { value: 'strict-local', label: 'Tylko lokalnie' },
  { value: 'prefer-local', label: 'Preferuj lokalne' },
  { value: 'ask-before-external', label: 'Pytaj przed chmurą' },
  { value: 'improve-when-needed', label: 'Chmura gdy trzeba' },
] as const

export function SettingsGeneralChatPanel() {
  const pushToast = useToastStore((state) => state.push)
  const okropekModeEnabled = useUIStore((state) => state.okropekModeEnabled)
  const setOkropekModeEnabled = useUIStore((state) => state.setOkropekModeEnabled)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [center, setCenter] = useState<SettingsControlCenterPayload | null>(null)
  const [workMode, setWorkMode] = useState<ChatWorkMode>(readChatWorkMode)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const payload = await fetchSettingsControlCenter()
      setCenter(payload)
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się wczytać ustawień czatu',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void reload()
  }, [reload])

  const save = async () => {
    if (!center) {
      return
    }
    setSaving(true)
    try {
      writeChatWorkMode(workMode)
      const stylePayload = await updateSettingsStyle({
        responseLength: center.style.responseLength,
        tone: center.style.tone,
        structure: center.style.structure,
        criticalityLevel: center.style.criticalityLevel,
        supportivenessLevel: center.style.supportivenessLevel,
        professionalDefault: center.style.professionalDefault,
      })
      const policyPayload = await updateSettingsPolicy({
        automationLevel: center.policy.automationLevel,
        verificationMode: center.policy.verificationMode,
        allowLocalFileRead: center.policy.allowLocalFileRead,
        allowLocalFileWrite: center.policy.allowLocalFileWrite,
        allowWebRead: center.policy.allowWebRead,
        allowExternalFallback: center.policy.allowExternalFallback,
        allowExternalAttachmentMetadata: center.policy.allowExternalAttachmentMetadata,
        allowExternalAttachmentContent: center.policy.allowExternalAttachmentContent,
        preferLocalLlm: center.policy.preferLocalLlm,
        defaultProviderPosture: center.policy.defaultProviderPosture,
        defaultPersonaProfile: center.policy.defaultPersonaProfile,
        jarvisAddressAs: center.policy.jarvisAddressAs,
        maxExternalAttachmentTextChars: center.policy.maxExternalAttachmentTextChars,
        maxDailyCost: center.policy.maxDailyCost,
        maxMonthlyCost: center.policy.maxMonthlyCost,
      })
      setCenter({ ...stylePayload, ...policyPayload, style: stylePayload.style, policy: policyPayload.policy })
      pushToast({
        type: 'success',
        title: 'Zapisano domyślne czatu',
        message: 'Nowe rozmowy i fallbacki użyją tych ustawień.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Zapis czatu nieudany',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setSaving(false)
    }
  }

  if (loading || !center) {
    return (
      <div className="border border-border-default bg-void p-4 text-sm text-text-secondary">
        Wczytuję zarządzanie czatem…
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="settings-general-chat">
      <div className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Zarządzanie czatem</div>
        <p className="mt-2 text-sm text-text-secondary">
          To są domyślne ustawienia Atnosa. Szybkie przełączniki w oknie rozmowy nadpisują je tylko dla
          bieżącej konwersacji.
        </p>
      </div>

      <section className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Domyślny tryb Atnosa</div>
        <div className="mt-3 flex flex-wrap gap-2">
          {CHAT_WORK_MODE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              title={option.hint}
              onClick={() => setWorkMode(option.value)}
              className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                workMode === option.value
                  ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="border border-border-default bg-panel p-4">
          <div className="label-eyebrow">Domyślna długość odpowiedzi</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {LENGTH_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() =>
                  setCenter((current) =>
                    current
                      ? {
                          ...current,
                          style: { ...current.style, responseLength: option.value },
                        }
                      : current,
                  )
                }
                className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                  center.style.responseLength === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="mt-4 label-eyebrow">Domyślny ton</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {TONE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() =>
                  setCenter((current) =>
                    current
                      ? { ...current, style: { ...current.style, tone: option.value } }
                      : current,
                  )
                }
                className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                  center.style.tone === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="border border-border-default bg-panel p-4">
          <div className="label-eyebrow">Domyślna persona</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {PERSONA_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() =>
                  setCenter((current) =>
                    current
                      ? {
                          ...current,
                          policy: { ...current.policy, defaultPersonaProfile: option.value },
                        }
                      : current,
                  )
                }
                className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                  center.policy.defaultPersonaProfile === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="mt-4 label-eyebrow">Domyślna postura providerów</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {POSTURE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() =>
                  setCenter((current) =>
                    current
                      ? {
                          ...current,
                          policy: { ...current.policy, defaultProviderPosture: option.value },
                        }
                      : current,
                  )
                }
                className={`rounded-pill border px-3 py-1.5 text-[11px] ${
                  center.policy.defaultProviderPosture === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Uprawnienia narzędzi czatu (globalne)</div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {(
            [
              ['allowLocalFileRead', 'Pliki: odczyt'],
              ['allowLocalFileWrite', 'Pliki: zapis'],
              ['allowWebRead', 'Internet / przeglądarka'],
              ['preferLocalLlm', 'Preferuj lokalne LLM'],
              ['allowExternalFallback', 'Fallback do chmury'],
            ] as const
          ).map(([key, label]) => (
            <label
              key={key}
              className="flex items-center justify-between gap-3 border border-border-default bg-void px-3 py-2 text-sm"
            >
              <span>{label}</span>
              <input
                type="checkbox"
                checked={Boolean(center.policy[key])}
                onChange={(event) =>
                  setCenter((current) =>
                    current
                      ? {
                          ...current,
                          policy: { ...current.policy, [key]: event.target.checked },
                        }
                      : current,
                  )
                }
              />
            </label>
          ))}
        </div>
      </section>

      <section className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Okropek</div>
        <p className="mt-2 text-sm text-text-secondary">
          Pływający towarzysz w shellu — głos i szybkie polecenia. Możesz go wyłączyć w każdej chwili.
        </p>
        <button
          type="button"
          onClick={() => setOkropekModeEnabled(!okropekModeEnabled)}
          className={`mt-3 rounded-pill border px-3 py-1.5 text-[11px] ${
            okropekModeEnabled
              ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
              : 'border-border-default text-text-secondary'
          }`}
        >
          {okropekModeEnabled ? 'Okropek: włączony' : 'Okropek: wyłączony'}
        </button>
      </section>

      <div className="flex justify-end">
        <button
          type="button"
          disabled={saving}
          onClick={() => void save()}
          className="border border-accent-primary bg-accent-primary/10 px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:opacity-60"
        >
          {saving ? 'Zapisuję…' : 'Zapisz domyślne czatu'}
        </button>
      </div>
    </div>
  )
}
