import { Check, ChevronDown, LoaderCircle, Save, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  approveYouTubeChannelManifest,
  approveYouTubePublishingPolicy,
  confirmYouTubeChannelIdentity,
  fetchOAuthConnections,
  saveYouTubeChannelIdentity,
  saveYouTubeChannelManifest,
  saveYouTubePublishingPolicy,
  startOAuthConnection,
  type OAuthConnectionSummary,
  type ProjectYouTubeChannelIdentitySnapshot,
} from '../lib/api'

type TextForm = Record<string, string>

const MANIFEST_FIELDS: Array<{ key: string; label: string; hint: string; multiline?: boolean }> = [
  { key: 'mission', label: 'Misja kanału', hint: 'Dlaczego kanał istnieje i jaką wartość dostarcza.' },
  { key: 'target_audience', label: 'Docelowy widz', hint: 'Kto ma oglądać materiały i w jakim języku.' },
  { key: 'viewer_promise', label: 'Obietnica dla widza', hint: 'Co widz dostaje po każdym materiale.' },
  { key: 'primary_topics', label: 'Tematy główne', hint: 'Zakres tematów, serii i formatów.' },
  { key: 'forbidden_topics', label: 'Tematy niedozwolone', hint: 'Treści poza strategią, ryzykowne lub niedopuszczalne.' },
  { key: 'tone', label: 'Ton', hint: 'Na przykład: rzeczowy, spokojny, suspense.' },
  { key: 'narration_style', label: 'Styl narracji', hint: 'Budowa zdań, energia i sposób wyjaśniania.' },
  { key: 'pace', label: 'Tempo', hint: 'Tempo narracji i rytm wizualny.' },
  { key: 'hook_structure', label: 'Struktura hooka', hint: 'Co musi wydarzyć się w pierwszych sekundach.' },
  { key: 'editing_style', label: 'Styl montażu', hint: 'Cięcia, ujęcia, dane na ekranie, przejścia.' },
  { key: 'subtitle_style', label: 'Styl napisów', hint: 'Pozycja, kontrast, liczba słów i animacja.' },
  { key: 'color_palette', label: 'Paleta kolorów', hint: 'Kolory rozpoznawalne i zakazane.' },
  { key: 'typography', label: 'Typografia', hint: 'Fonty, wielkości i reguły czytelności.' },
  { key: 'music_style', label: 'Styl muzyki', hint: 'Rodzaj muzyki, poziomy i ograniczenia licencyjne.' },
  { key: 'voice_character', label: 'Charakter głosu', hint: 'Język, emocja, tempo i profil TTS.' },
  { key: 'duration_range_seconds', label: 'Długość materiałów', hint: 'Na przykład: 35-55 sekund.' },
  { key: 'thumbnail_rules', label: 'Zasady miniatur', hint: 'Kompozycja, czytelność, zakazane schematy.' },
  { key: 'title_rules', label: 'Zasady tytułów', hint: 'Prawdziwość, długość, styl i zakazy.' },
  { key: 'publishing_cadence', label: 'Częstotliwość publikacji', hint: 'Zakładana rytmika, nie zgoda na publikację.' },
  { key: 'recognizable_elements', label: 'Elementy rozpoznawalne', hint: 'Minimum trzy elementy wyróżniające kanał.', multiline: true },
]

const INPUT_CLASS = 'mt-1 w-full rounded-md border border-border-default bg-app px-2.5 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary'
const LABEL_CLASS = 'block text-xs font-medium text-text-secondary'

function asText(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(', ')
  if (value == null) return ''
  return String(value)
}

function manifestForm(snapshot: ProjectYouTubeChannelIdentitySnapshot | null): TextForm {
  const source = snapshot?.manifest.value ?? {}
  return Object.fromEntries(MANIFEST_FIELDS.map(({ key }) => [key, asText(source[key])]))
}

function policyForm(snapshot: ProjectYouTubeChannelIdentitySnapshot | null): TextForm {
  const source = snapshot?.publishing_policy.value ?? {}
  return {
    allowed_channel_ids: asText(source.allowed_channel_ids ?? snapshot?.identity?.youtube_channel_id),
    allowed_days: asText(source.allowed_days),
    allowed_hours_local: asText(source.allowed_hours_local),
    max_publications_per_day: asText(source.max_publications_per_day ?? 1),
    minimum_quality_score: asText(source.minimum_quality_score ?? 90),
    visibility_mode: asText(source.visibility_mode ?? 'private'),
    require_private_first: String(source.require_private_first ?? true),
    require_quality_gate: String(source.require_quality_gate ?? true),
    require_render_validation: String(source.require_render_validation ?? true),
    notify_owner: String(source.notify_owner ?? true),
    stop_conditions: asText(source.stop_conditions),
  }
}

function identityForm(snapshot: ProjectYouTubeChannelIdentitySnapshot | null): TextForm {
  const identity = snapshot?.identity
  return {
    account_binding_key: identity?.account_binding_key ?? '',
    youtube_channel_id: identity?.youtube_channel_id ?? '',
    channel_handle: identity?.channel_handle ?? '',
    channel_url: identity?.channel_url ?? '',
    display_name: identity?.display_name ?? '',
    owner_label: identity?.owner_label ?? '',
    language_code: identity?.language_code ?? 'en',
    target_market: identity?.target_market ?? '',
    niche: identity?.niche ?? '',
    oauth_connection_id: identity?.oauth_connection_id == null ? '' : String(identity.oauth_connection_id),
  }
}

function listValue(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function boolValue(value: string): boolean {
  return value === 'true'
}

export function YouTubeChannelSetupPanel({
  projectId,
  snapshot,
  onUpdated,
}: {
  projectId: number
  snapshot: ProjectYouTubeChannelIdentitySnapshot
  onUpdated: () => Promise<void>
}) {
  const [connections, setConnections] = useState<OAuthConnectionSummary[]>([])
  const [identity, setIdentity] = useState<TextForm>(() => identityForm(snapshot))
  const [manifest, setManifest] = useState<TextForm>(() => manifestForm(snapshot))
  const [policy, setPolicy] = useState<TextForm>(() => policyForm(snapshot))
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [authorizationUrl, setAuthorizationUrl] = useState<string | null>(null)

  useEffect(() => {
    setIdentity(identityForm(snapshot))
    setManifest(manifestForm(snapshot))
    setPolicy(policyForm(snapshot))
  }, [snapshot])

  useEffect(() => {
    let active = true
    void fetchOAuthConnections()
      .then((items) => {
        if (active) setConnections(items.filter((item) => item.connectorKey === 'youtube-studio'))
      })
      .catch(() => {
        if (active) setConnections([])
      })
    return () => { active = false }
  }, [])

  const run = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key)
    setError(null)
    setNotice(null)
    try {
      await action()
      await onUpdated()
      setNotice(success)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Operacja konfiguracji kanału nie powiodła się.')
    } finally {
      setBusy(null)
    }
  }

  const connectedYouTube = connections.filter((item) => item.status === 'connected')
  const selectedConnection = connectedYouTube.find((item) => item.id === Number(identity.oauth_connection_id))
  const channelId = snapshot.identity?.youtube_channel_id ?? identity.youtube_channel_id
  const manifestReady = snapshot.manifest.missing_fields.length === 0
  const policyReady = snapshot.publishing_policy.missing_fields.length === 0

  const chooseConnection = (connectionId: string) => {
    const connection = connectedYouTube.find((item) => item.id === Number(connectionId))
    const binding = connection?.metadata.content_account_id
    setIdentity((current) => ({
      ...current,
      oauth_connection_id: connectionId,
      account_binding_key: typeof binding === 'string' && binding.startsWith('youtube:') ? binding : current.account_binding_key,
      youtube_channel_id: connectionId === current.oauth_connection_id ? current.youtube_channel_id : '',
    }))
  }

  const saveIdentity = () => run('identity', async () => {
    await saveYouTubeChannelIdentity(projectId, {
      accountBindingKey: identity.account_binding_key,
      youtubeChannelId: identity.youtube_channel_id || null,
      channelHandle: identity.channel_handle || null,
      channelUrl: identity.channel_url || null,
      displayName: identity.display_name || null,
      ownerLabel: identity.owner_label || null,
      languageCode: identity.language_code || null,
      targetMarket: identity.target_market || null,
      niche: identity.niche || null,
      oauthConnectionId: identity.oauth_connection_id ? Number(identity.oauth_connection_id) : null,
    })
  }, 'Tożsamość zapisano. Teraz wykonaj odczyt kanału przez YouTube.')

  const beginOAuth = async () => {
    const binding = identity.account_binding_key.trim()
    if (!binding.startsWith('youtube:')) {
      setError('Najpierw wpisz trwałe powiązanie w formacie youtube:owner@example.com.')
      return
    }
    setBusy('oauth')
    setError(null)
    setNotice(null)
    try {
      const result = await startOAuthConnection('youtube-studio', { contentAccountId: binding })
      if (!result.authorizationUrl) throw new Error('Backend nie zwrócił linku OAuth.')
      setAuthorizationUrl(result.authorizationUrl)
      setNotice(`Utworzono połączenie OAuth #${result.connectionId}. Otwórz link, zaloguj się na właściwe konto i zaakceptuj zgody.`)
      await onUpdated()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się rozpocząć OAuth.')
    } finally {
      setBusy(null)
    }
  }

  const saveManifest = () => run('manifest', async () => {
    await saveYouTubeChannelManifest(projectId, manifest)
  }, 'Manifest zapisano. Zatwierdzenie będzie możliwe po potwierdzeniu kanału.')

  const savePolicy = () => run('policy', async () => {
    await saveYouTubePublishingPolicy(projectId, {
      allowed_channel_ids: listValue(policy.allowed_channel_ids),
      allowed_days: listValue(policy.allowed_days),
      allowed_hours_local: listValue(policy.allowed_hours_local),
      max_publications_per_day: Number(policy.max_publications_per_day),
      minimum_quality_score: Number(policy.minimum_quality_score),
      visibility_mode: policy.visibility_mode,
      require_private_first: boolValue(policy.require_private_first),
      require_quality_gate: boolValue(policy.require_quality_gate),
      require_render_validation: boolValue(policy.require_render_validation),
      notify_owner: boolValue(policy.notify_owner),
      stop_conditions: listValue(policy.stop_conditions),
    })
  }, 'Politykę zapisano. Zatwierdzenie nie uruchamia publikacji.')

  return (
    <section className="mt-4 rounded-lg border border-border-default bg-app/35 p-3" data-testid="youtube-channel-setup-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow">YOUTUBE CHANNEL SETUP</div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-text-primary">
            Konfiguracja jest etapowa: wybierz trwałe połączenie OAuth, odczytaj kanał przez API, potwierdź jego ID, a następnie zatwierdź manifest i politykę.
          </p>
        </div>
        <span className="rounded-pill border border-sem-warning/40 bg-sem-warning/10 px-2.5 py-1 text-[10px] font-semibold text-sem-warning">
          BEZ UPLOADU
        </span>
      </div>

      {notice ? <p className="mt-3 rounded-md border border-sem-success/40 bg-sem-success/10 px-3 py-2 text-xs text-sem-success">{notice}</p> : null}
      {error ? <p className="mt-3 rounded-md border border-sem-danger/40 bg-sem-danger/10 px-3 py-2 text-xs text-sem-danger">{error}</p> : null}

      <details className="mt-4 rounded-md border border-border-default bg-panel/55 p-3" open>
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-text-primary">
          <span>1. Połączenie i tożsamość kanału</span>
          <ChevronDown size={15} className="text-text-tertiary" />
        </summary>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className={LABEL_CLASS}>
            Trwałe powiązanie konta
            <input className={INPUT_CLASS} value={identity.account_binding_key} onChange={(event) => setIdentity({ ...identity, account_binding_key: event.target.value })} placeholder="youtube:owner@example.com" autoComplete="off" />
          </label>
          <label className={LABEL_CLASS}>
            Aktywne połączenie YouTube OAuth
            <select className={INPUT_CLASS} value={identity.oauth_connection_id} onChange={(event) => chooseConnection(event.target.value)}>
              <option value="">Wybierz połączenie OAuth</option>
              {connectedYouTube.map((connection) => <option key={connection.id} value={connection.id}>#{connection.id} · {connection.displayName} · konto {connection.accountId ?? 'bez mapowania'}</option>)}
            </select>
          </label>
          <label className={LABEL_CLASS}>
            Nazwa robocza kanału
            <input className={INPUT_CLASS} value={identity.display_name} onChange={(event) => setIdentity({ ...identity, display_name: event.target.value })} placeholder="Nie jest identyfikatorem kanału" />
          </label>
          <label className={LABEL_CLASS}>
            Właściciel / operator
            <input className={INPUT_CLASS} value={identity.owner_label} onChange={(event) => setIdentity({ ...identity, owner_label: event.target.value })} placeholder="Nazwa operatora" />
          </label>
          <label className={LABEL_CLASS}>
            Język materiałów
            <input className={INPUT_CLASS} value={identity.language_code} onChange={(event) => setIdentity({ ...identity, language_code: event.target.value })} placeholder="en" />
          </label>
          <label className={LABEL_CLASS}>
            Rynek docelowy
            <input className={INPUT_CLASS} value={identity.target_market} onChange={(event) => setIdentity({ ...identity, target_market: event.target.value })} placeholder="US, UK, global" />
          </label>
          <label className={`${LABEL_CLASS} md:col-span-2`}>
            Nisza / zakres projektu
            <textarea className={INPUT_CLASS} rows={2} value={identity.niche} onChange={(event) => setIdentity({ ...identity, niche: event.target.value })} placeholder="Opis zakresu kanału. Nie wpisuj sekretów." />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={saveIdentity} disabled={busy !== null} className="inline-flex items-center gap-1.5 rounded-md border border-accent-primary/50 bg-accent-primary/10 px-3 py-2 text-xs font-semibold text-accent-primary disabled:opacity-50">
            {busy === 'identity' ? <LoaderCircle size={14} className="animate-spin" /> : <Save size={14} />} Zapisz powiązanie
          </button>
          <span className="text-xs text-text-tertiary">{selectedConnection ? `Wybrane OAuth #${selectedConnection.id}` : 'Wymagane jest istniejące połączenie YouTube OAuth.'}</span>
        </div>
        {connectedYouTube.length === 0 ? (
          <div className="mt-3 rounded-md border border-sem-warning/35 bg-sem-warning/5 px-3 py-3">
            <p className="text-xs leading-5 text-sem-warning">Brak aktywnego tokenu YouTube. Nie można odczytać kanału ani rozpocząć uploadu.</p>
            <button type="button" onClick={() => void beginOAuth()} disabled={busy !== null} className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-sem-warning/50 px-3 py-2 text-xs font-semibold text-sem-warning disabled:opacity-50">
              {busy === 'oauth' ? <LoaderCircle size={14} className="animate-spin" /> : <ShieldCheck size={14} />} Wygeneruj bezpieczny link OAuth
            </button>
            {authorizationUrl ? (
              <a className="mt-2 block text-xs font-semibold text-accent-primary underline underline-offset-4" href={authorizationUrl} target="_blank" rel="noreferrer">
                Otwórz Google OAuth w nowym oknie
              </a>
            ) : null}
          </div>
        ) : null}
        <div className="mt-3 rounded-md border border-border-default bg-app/60 px-3 py-2 text-xs">
          <div className="font-mono text-text-primary">{channelId || 'UC… zostanie odczytane przez YouTube API'}</div>
          <p className="mt-1 text-text-secondary">ID kanału nie jest wprowadzane ręcznie. Wróć do przycisku „Sprawdź identyfikator przez YouTube” powyżej po zapisie powiązania.</p>
        </div>
        {channelId && !snapshot.identity?.owner_confirmed ? (
          <button type="button" disabled={busy !== null} onClick={() => void run('confirm', () => confirmYouTubeChannelIdentity(projectId, channelId), 'Kanał został jawnie potwierdzony przez operatora.')} className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-sem-success/40 bg-sem-success/10 px-3 py-2 text-xs font-semibold text-sem-success disabled:opacity-50">
            {busy === 'confirm' ? <LoaderCircle size={14} className="animate-spin" /> : <Check size={14} />} Potwierdzam kanał {channelId}
          </button>
        ) : null}
      </details>

      <details className="mt-3 rounded-md border border-border-default bg-panel/55 p-3">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-text-primary">
          <span>2. Channel Identity Manifest</span>
          <span className="text-[10px] text-text-tertiary">{snapshot.manifest.status}</span>
        </summary>
        <p className="mt-3 text-xs leading-5 text-text-secondary">To jest profil kreatywny kanału, nie prompt ani wynik modelu. Puste pola pozostawiają manifest w trybie draft.</p>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {MANIFEST_FIELDS.map((field) => (
            <label key={field.key} className={LABEL_CLASS}>
              {field.label}
              {field.multiline ? (
                <textarea className={INPUT_CLASS} rows={3} value={manifest[field.key] ?? ''} onChange={(event) => setManifest({ ...manifest, [field.key]: event.target.value })} placeholder={field.hint} />
              ) : (
                <input className={INPUT_CLASS} value={manifest[field.key] ?? ''} onChange={(event) => setManifest({ ...manifest, [field.key]: event.target.value })} placeholder={field.hint} />
              )}
            </label>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={saveManifest} disabled={busy !== null} className="inline-flex items-center gap-1.5 rounded-md border border-accent-primary/50 bg-accent-primary/10 px-3 py-2 text-xs font-semibold text-accent-primary disabled:opacity-50">
            {busy === 'manifest' ? <LoaderCircle size={14} className="animate-spin" /> : <Save size={14} />} Zapisz manifest
          </button>
          <button type="button" onClick={() => void run('manifest-approve', () => approveYouTubeChannelManifest(projectId), 'Manifest został zatwierdzony.')} disabled={busy !== null || !snapshot.identity?.owner_confirmed || !manifestReady} className="inline-flex items-center gap-1.5 rounded-md border border-sem-success/40 bg-sem-success/10 px-3 py-2 text-xs font-semibold text-sem-success disabled:opacity-50">
            {busy === 'manifest-approve' ? <LoaderCircle size={14} className="animate-spin" /> : <Check size={14} />} Zatwierdź manifest
          </button>
          {snapshot.manifest.missing_fields.length > 0 ? <span className="text-xs text-sem-warning">Brakuje: {snapshot.manifest.missing_fields.join(', ')}</span> : null}
        </div>
      </details>

      <details className="mt-3 rounded-md border border-border-default bg-panel/55 p-3">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-text-primary">
          <span>3. Channel Publishing Policy</span>
          <span className="text-[10px] text-text-tertiary">{snapshot.publishing_policy.status}</span>
        </summary>
        <p className="mt-3 text-xs leading-5 text-text-secondary">Polityka definiuje tylko dopuszczalny zakres automatyzacji. Zapis ani zatwierdzenie nie wykonują uploadu.</p>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className={LABEL_CLASS}>Dozwolone identyfikatory kanałów (oddziel przecinkami)<input className={INPUT_CLASS} value={policy.allowed_channel_ids} onChange={(event) => setPolicy({ ...policy, allowed_channel_ids: event.target.value })} placeholder="UC..." /></label>
          <label className={LABEL_CLASS}>Dni publikacji (oddziel przecinkami)<input className={INPUT_CLASS} value={policy.allowed_days} onChange={(event) => setPolicy({ ...policy, allowed_days: event.target.value })} placeholder="Mon, Wed, Fri" /></label>
          <label className={LABEL_CLASS}>Godziny lokalne (oddziel przecinkami)<input className={INPUT_CLASS} value={policy.allowed_hours_local} onChange={(event) => setPolicy({ ...policy, allowed_hours_local: event.target.value })} placeholder="09:00-12:00, 17:00-20:00" /></label>
          <label className={LABEL_CLASS}>Maks. publikacji dziennie<input type="number" min="1" max="24" className={INPUT_CLASS} value={policy.max_publications_per_day} onChange={(event) => setPolicy({ ...policy, max_publications_per_day: event.target.value })} /></label>
          <label className={LABEL_CLASS}>Minimalny wynik jakości (0-100)<input type="number" min="0" max="100" className={INPUT_CLASS} value={policy.minimum_quality_score} onChange={(event) => setPolicy({ ...policy, minimum_quality_score: event.target.value })} /></label>
          <label className={LABEL_CLASS}>Domyślna widoczność<select className={INPUT_CLASS} value={policy.visibility_mode} onChange={(event) => setPolicy({ ...policy, visibility_mode: event.target.value })}><option value="private">private</option><option value="unlisted">unlisted</option><option value="scheduled">scheduled</option><option value="public">public</option></select></label>
          <label className={`${LABEL_CLASS} md:col-span-2`}>Warunki zatrzymania (oddziel przecinkami)<textarea className={INPUT_CLASS} rows={2} value={policy.stop_conditions} onChange={(event) => setPolicy({ ...policy, stop_conditions: event.target.value })} placeholder="OAuth unavailable, QA P0, render validation failed" /></label>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {[
            ['require_private_first', 'Wymagaj pierwszego renderu private'],
            ['require_quality_gate', 'Wymagaj Quality Gate'],
            ['require_render_validation', 'Wymagaj FFprobe i walidacji renderu'],
            ['notify_owner', 'Wysyłaj powiadomienie do właściciela'],
          ].map(([key, label]) => (
            <label key={key} className="flex items-center gap-2 rounded-md border border-border-default bg-app/60 px-3 py-2 text-xs text-text-secondary">
              <input type="checkbox" checked={boolValue(policy[key])} onChange={(event) => setPolicy({ ...policy, [key]: String(event.target.checked) })} /> {label}
            </label>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={savePolicy} disabled={busy !== null} className="inline-flex items-center gap-1.5 rounded-md border border-accent-primary/50 bg-accent-primary/10 px-3 py-2 text-xs font-semibold text-accent-primary disabled:opacity-50">
            {busy === 'policy' ? <LoaderCircle size={14} className="animate-spin" /> : <Save size={14} />} Zapisz politykę
          </button>
          <button type="button" onClick={() => void run('policy-approve', () => approveYouTubePublishingPolicy(projectId), 'Polityka publikacji została zatwierdzona.')} disabled={busy !== null || !snapshot.identity?.owner_confirmed || !policyReady} className="inline-flex items-center gap-1.5 rounded-md border border-sem-success/40 bg-sem-success/10 px-3 py-2 text-xs font-semibold text-sem-success disabled:opacity-50">
            {busy === 'policy-approve' ? <LoaderCircle size={14} className="animate-spin" /> : <ShieldCheck size={14} />} Zatwierdź politykę
          </button>
          {snapshot.publishing_policy.missing_fields.length > 0 ? <span className="text-xs text-sem-warning">Brakuje: {snapshot.publishing_policy.missing_fields.join(', ')}</span> : null}
        </div>
      </details>
    </section>
  )
}
