export type OkropekVisualState = 'idle' | 'thinking' | 'executing' | 'listening' | 'error'

export interface OkropekAssetPack {
  mode: 'fallback-core' | 'svg-states'
  name: string
  version: string
  notes?: string
  hideDefaultFeet?: boolean
  states?: Partial<Record<OkropekVisualState, string>>
}

/**
 * Okropek Claude v1 — premium cosmic onyx orb-core micro-mascot.
 *
 * Each state is a self-contained inline <svg> at viewBox="0 0 96 96",
 * no external fonts, no external URLs, no scripts. Reads cleanly at
 * 32px / 48px / 64px. Differentiation between states is visual:
 *
 *   idle       — cyan core, relaxed eye-slit, soft ambient aura
 *   thinking   — narrower eye, dashed inner ring, denser cyan glow
 *   executing  — full intensity core, triple rings, ambient particle dots
 *   listening  — warm amber accent (#ff8e5b) replaces cyan; awaiting cue
 *   error      — muted coral (#ff7a6e), dashed body stroke, dimmed eye
 *
 * Motion (bob, ring rotation, pulse) is the runtime's responsibility —
 * these SVGs render the snapshot pose for each state.
 */
export const okropekAssetPack: OkropekAssetPack = {
  mode: 'svg-states',
  name: 'Okropek Claude v1',
  version: '2026-05-12',
  hideDefaultFeet: true,
  states: {
    idle: String.raw`<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Okropek idle">
  <defs>
    <radialGradient id="okropek-idle-body" cx="38%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#1d2840"/>
      <stop offset="55%" stop-color="#08101e"/>
      <stop offset="100%" stop-color="#020409"/>
    </radialGradient>
    <radialGradient id="okropek-idle-core" cx="50%" cy="52%" r="50%">
      <stop offset="0%" stop-color="#c2e5ff" stop-opacity="0.7"/>
      <stop offset="50%" stop-color="#86d0ff" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="okropek-idle-aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.18"/>
      <stop offset="65%" stop-color="#86d0ff" stop-opacity="0.04"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="okropek-idle-eye" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.4"/>
      <stop offset="50%" stop-color="#d6efff" stop-opacity="1"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0.4"/>
    </linearGradient>
  </defs>
  <circle cx="48" cy="48" r="42" fill="url(#okropek-idle-aura)"/>
  <ellipse cx="48" cy="86" rx="20" ry="3" fill="#000000" opacity="0.5"/>
  <ellipse cx="48" cy="86" rx="15" ry="2.2" fill="#86d0ff" opacity="0.16"/>
  <path d="M 48 12 C 34 12, 28 21, 28 33 L 28 60 C 28 70, 34 78, 48 78 C 62 78, 68 70, 68 60 L 68 33 C 68 21, 62 12, 48 12 Z" fill="url(#okropek-idle-body)" stroke="rgba(134,208,255,0.26)" stroke-width="0.8"/>
  <ellipse cx="48" cy="48" rx="14" ry="20" fill="url(#okropek-idle-core)"/>
  <ellipse cx="38" cy="22" rx="4.5" ry="2.4" fill="rgba(214,239,255,0.42)"/>
  <rect x="36" y="45" width="24" height="3.6" rx="1.8" fill="url(#okropek-idle-eye)"/>
</svg>`,

    thinking: String.raw`<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Okropek thinking">
  <defs>
    <radialGradient id="okropek-thinking-body" cx="38%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#1d2840"/>
      <stop offset="55%" stop-color="#08101e"/>
      <stop offset="100%" stop-color="#020409"/>
    </radialGradient>
    <radialGradient id="okropek-thinking-core" cx="50%" cy="52%" r="48%">
      <stop offset="0%" stop-color="#c2e5ff" stop-opacity="0.85"/>
      <stop offset="35%" stop-color="#86d0ff" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="okropek-thinking-aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.25"/>
      <stop offset="65%" stop-color="#86d0ff" stop-opacity="0.07"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="okropek-thinking-eye" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.45"/>
      <stop offset="50%" stop-color="#d6efff" stop-opacity="1"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0.45"/>
    </linearGradient>
  </defs>
  <circle cx="48" cy="48" r="44" fill="url(#okropek-thinking-aura)"/>
  <ellipse cx="48" cy="86" rx="20" ry="3" fill="#000000" opacity="0.5"/>
  <ellipse cx="48" cy="86" rx="15" ry="2.2" fill="#86d0ff" opacity="0.2"/>
  <path d="M 48 12 C 34 12, 28 21, 28 33 L 28 60 C 28 70, 34 78, 48 78 C 62 78, 68 70, 68 60 L 68 33 C 68 21, 62 12, 48 12 Z" fill="url(#okropek-thinking-body)" stroke="rgba(134,208,255,0.32)" stroke-width="0.8"/>
  <ellipse cx="48" cy="48" rx="14" ry="20" fill="url(#okropek-thinking-core)"/>
  <ellipse cx="48" cy="48" rx="9" ry="14" fill="none" stroke="rgba(134,208,255,0.4)" stroke-width="0.6" stroke-dasharray="2 3"/>
  <ellipse cx="38" cy="22" rx="4.5" ry="2.4" fill="rgba(214,239,255,0.42)"/>
  <rect x="40" y="45.4" width="16" height="2.8" rx="1.4" fill="url(#okropek-thinking-eye)"/>
</svg>`,

    executing: String.raw`<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Okropek executing">
  <defs>
    <radialGradient id="okropek-exec-body" cx="38%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#1d2840"/>
      <stop offset="55%" stop-color="#08101e"/>
      <stop offset="100%" stop-color="#020409"/>
    </radialGradient>
    <radialGradient id="okropek-exec-core" cx="50%" cy="52%" r="55%">
      <stop offset="0%" stop-color="#d6efff" stop-opacity="1"/>
      <stop offset="35%" stop-color="#c2e5ff" stop-opacity="0.6"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="okropek-exec-aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.38"/>
      <stop offset="55%" stop-color="#86d0ff" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="okropek-exec-eye" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#86d0ff" stop-opacity="0.6"/>
      <stop offset="50%" stop-color="#d6efff" stop-opacity="1"/>
      <stop offset="100%" stop-color="#86d0ff" stop-opacity="0.6"/>
    </linearGradient>
  </defs>
  <circle cx="48" cy="48" r="46" fill="url(#okropek-exec-aura)"/>
  <circle cx="20" cy="44" r="0.9" fill="#c2e5ff" opacity="0.6"/>
  <circle cx="76" cy="40" r="0.9" fill="#c2e5ff" opacity="0.6"/>
  <circle cx="22" cy="60" r="0.7" fill="#86d0ff" opacity="0.5"/>
  <circle cx="74" cy="58" r="0.7" fill="#86d0ff" opacity="0.5"/>
  <circle cx="28" cy="26" r="0.6" fill="#d6efff" opacity="0.5"/>
  <circle cx="68" cy="68" r="0.6" fill="#d6efff" opacity="0.5"/>
  <ellipse cx="48" cy="86" rx="22" ry="3.4" fill="#000000" opacity="0.55"/>
  <ellipse cx="48" cy="86" rx="17" ry="2.5" fill="#86d0ff" opacity="0.28"/>
  <path d="M 48 12 C 34 12, 28 21, 28 33 L 28 60 C 28 70, 34 78, 48 78 C 62 78, 68 70, 68 60 L 68 33 C 68 21, 62 12, 48 12 Z" fill="url(#okropek-exec-body)" stroke="rgba(134,208,255,0.45)" stroke-width="0.9"/>
  <ellipse cx="48" cy="48" rx="14" ry="20" fill="url(#okropek-exec-core)"/>
  <ellipse cx="48" cy="48" rx="10" ry="15" fill="none" stroke="rgba(134,208,255,0.45)" stroke-width="0.6"/>
  <ellipse cx="48" cy="48" rx="6.5" ry="10" fill="none" stroke="rgba(194,229,255,0.55)" stroke-width="0.5"/>
  <ellipse cx="38" cy="22" rx="4.5" ry="2.4" fill="rgba(214,239,255,0.5)"/>
  <rect x="34" y="44.8" width="28" height="3.8" rx="1.9" fill="url(#okropek-exec-eye)"/>
</svg>`,

    listening: String.raw`<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Okropek listening">
  <defs>
    <radialGradient id="okropek-listen-body" cx="38%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#1f2538"/>
      <stop offset="55%" stop-color="#0d121d"/>
      <stop offset="100%" stop-color="#040608"/>
    </radialGradient>
    <radialGradient id="okropek-listen-core" cx="50%" cy="52%" r="52%">
      <stop offset="0%" stop-color="#ffd5b8" stop-opacity="0.8"/>
      <stop offset="40%" stop-color="#ff8e5b" stop-opacity="0.3"/>
      <stop offset="100%" stop-color="#ff8e5b" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="okropek-listen-aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ff8e5b" stop-opacity="0.22"/>
      <stop offset="65%" stop-color="#ff8e5b" stop-opacity="0.06"/>
      <stop offset="100%" stop-color="#ff8e5b" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="okropek-listen-eye" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ff8e5b" stop-opacity="0.45"/>
      <stop offset="50%" stop-color="#ffe5d2" stop-opacity="1"/>
      <stop offset="100%" stop-color="#ff8e5b" stop-opacity="0.45"/>
    </linearGradient>
  </defs>
  <circle cx="48" cy="48" r="44" fill="url(#okropek-listen-aura)"/>
  <ellipse cx="48" cy="86" rx="20" ry="3" fill="#000000" opacity="0.5"/>
  <ellipse cx="48" cy="86" rx="15" ry="2.2" fill="#ff8e5b" opacity="0.18"/>
  <path d="M 48 12 C 34 12, 28 21, 28 33 L 28 60 C 28 70, 34 78, 48 78 C 62 78, 68 70, 68 60 L 68 33 C 68 21, 62 12, 48 12 Z" fill="url(#okropek-listen-body)" stroke="rgba(255,142,91,0.32)" stroke-width="0.8"/>
  <ellipse cx="48" cy="48" rx="14" ry="20" fill="url(#okropek-listen-core)"/>
  <ellipse cx="48" cy="48" rx="11" ry="16" fill="none" stroke="rgba(255,142,91,0.35)" stroke-width="0.6"/>
  <ellipse cx="38" cy="22" rx="4.5" ry="2.4" fill="rgba(255,229,210,0.4)"/>
  <rect x="36" y="45.2" width="24" height="3.4" rx="1.7" fill="url(#okropek-listen-eye)"/>
  <circle cx="48" cy="56" r="0.9" fill="#ffe5d2" opacity="0.6"/>
</svg>`,

    error: String.raw`<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Okropek error">
  <defs>
    <radialGradient id="okropek-err-body" cx="38%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#1a1f30"/>
      <stop offset="55%" stop-color="#0a0f1a"/>
      <stop offset="100%" stop-color="#030508"/>
    </radialGradient>
    <radialGradient id="okropek-err-core" cx="50%" cy="52%" r="45%">
      <stop offset="0%" stop-color="#ff7a6e" stop-opacity="0.55"/>
      <stop offset="50%" stop-color="#ff7a6e" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#ff7a6e" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="okropek-err-aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ff7a6e" stop-opacity="0.14"/>
      <stop offset="65%" stop-color="#ff7a6e" stop-opacity="0.03"/>
      <stop offset="100%" stop-color="#ff7a6e" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="okropek-err-eye" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ff7a6e" stop-opacity="0.3"/>
      <stop offset="50%" stop-color="#ffb5ac" stop-opacity="0.85"/>
      <stop offset="100%" stop-color="#ff7a6e" stop-opacity="0.3"/>
    </linearGradient>
  </defs>
  <circle cx="48" cy="48" r="40" fill="url(#okropek-err-aura)"/>
  <ellipse cx="48" cy="86" rx="18" ry="2.8" fill="#000000" opacity="0.6"/>
  <ellipse cx="48" cy="86" rx="13" ry="2" fill="#ff7a6e" opacity="0.14"/>
  <path d="M 48 12 C 34 12, 28 21, 28 33 L 28 60 C 28 70, 34 78, 48 78 C 62 78, 68 70, 68 60 L 68 33 C 68 21, 62 12, 48 12 Z" fill="url(#okropek-err-body)" stroke="rgba(255,122,110,0.3)" stroke-width="0.8" stroke-dasharray="1.5 2"/>
  <ellipse cx="48" cy="48" rx="12" ry="17" fill="url(#okropek-err-core)"/>
  <ellipse cx="38" cy="22" rx="3.5" ry="1.8" fill="rgba(214,225,225,0.25)"/>
  <rect x="40" y="45.8" width="16" height="2.4" rx="1.2" fill="url(#okropek-err-eye)"/>
</svg>`,
  },
}
