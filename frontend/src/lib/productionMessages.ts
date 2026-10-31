export function productionSubmissionBlocker(text: string, localRyanSelected = false): string {
  const normalized = text.toLowerCase().replace(/ł/g, 'l').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (/\b(glos\w*|voice)\s+ryan\b|\bryan\s+b\b|\bbez\s+platnych\s+(?:api|uslug)\b|\bno\s+paid\s+(?:apis?|services?|tools?)\b|\bbezplatnych\s+narzedzi\b|\bbezplatnie\b|\b(?:wylacznie|tylko)\s+darmowe\s+narzedzia\b|\bfree\s+tools\s+only\b|\bbez\s+kosztow\s+api\b|\b(?:budzet|budget)\s*[:=]?\s*(?:usd\s*|\$\s*)?0(?:[.,]0+)?(?!\d|[.,]\d)/.test(normalized)) {
    return localRyanSelected ? '' : 'Wybierz lokalnego Ryana B, aby zachować żądany głos i wyłączyć płatnych dostawców. Nie zamienię głosu ani nie uruchomię płatnego API.'
  }
  return ''
}

export function productionStepLabel(step: string | null | undefined): string {
  const raw = (step ?? '').trim()
  if (!raw) return 'Start produkcji — oczekiwanie na pierwszy etap'
  const name = raw.split('/')[0] ?? ''
  if (name === 'script_ready') return 'Zakończenie etapu scenariusza'
  if (name === 'preflight') return 'Sprawdzanie połączeń i materiałów'
  if (name === 'waiting_for_runtime') return 'Oczekiwanie na wolny model'
  if (name === 'script_draft') return 'Pisanie pierwszej wersji scenariusza'
  if (name.startsWith('script_repair')) return 'Poprawianie scenariusza'
  if (name.startsWith('script_review')) return 'Sprawdzanie scenariusza'
  if (name.startsWith('script_calculation')) return 'Sprawdzanie założeń i obliczeń'
  if (name.startsWith('script')) return 'Przygotowywanie scenariusza'
  if (name === 'asset_review') return 'Grafiki czekają na ocenę i zatwierdzenie'
  if (name === 'assets' && raw.startsWith('assets/image_review/')) return 'Automatyczne sprawdzanie wygenerowanej grafiki'
  if (name === 'assets' && raw.startsWith('assets/image_local/')) return 'Generowanie ilustracji w ComfyUI'
  if (name === 'image_generation') return 'Przygotowywanie ilustracji'
  if (name.startsWith('asset')) return 'Dobieranie scen i materiałów'
  if (name.startsWith('scene_quality')) return 'Poprawianie wadliwej sceny'
  if (name.startsWith('scene_timing')) return 'Dopasowywanie scen do narracji'
  if (name.startsWith('voice_pronunciation')) return 'Poprawianie wymowy'
  if (name.startsWith('voice_recheck')) return 'Ponowne sprawdzanie wymowy'
  if (name.startsWith('voice_qa')) return 'Sprawdzanie wymowy'
  if (name.startsWith('voice')) return 'Nagrywanie narracji'
  if (name.startsWith('caption')) return 'Synchronizowanie napisów'
  if (name.startsWith('semantic') || name.startsWith('media_qa') || ['qa', 'quality_review'].includes(name)) return 'Kontrola jakości rolki'
  if (name.startsWith('cover')) return 'Przygotowywanie miniatury'
  if (name.startsWith('scene') || ['render', 'rendering', 'remotion_render', 'edit', 'editing', 'assembly'].includes(name)) return 'Montaż i renderowanie'
  if (['package', 'packaging', 'upload_package'].includes(name)) return 'Przygotowywanie pakietu'
  if (name === 'ready') return 'Produkcja ukończona'
  return `Etap: ${raw.replace(/_/g, ' ')}`
}

export type ProductionBlockerKind = 'script' | 'assets' | 'resource' | 'integrity' | 'other'

export interface ProductionBlockerIssue {
  code: string
  segment_ids: string[]
  detail: string
  message?: string
}

const SCRIPT_ISSUES: Record<string, string> = {
  practical_share: 'Za mało konkretnych działań dla widza. Wskazane sceny powinny jasno mówić, co widz może zrobić.',
  word_budget: 'Długość narracji nie mieści się w wymaganym zakresie.',
  incomplete_sentence: 'Narracja zawiera niepełne zdanie. Popraw je, zachowując sens sceny.',
  sentence_punctuation: 'Popraw interpunkcję i zakończenia zdań w narracji.',
  narration_punctuation: 'Popraw interpunkcję i zakończenia zdań w narracji.',
  long_segment: 'Wskazana scena jest zbyt długa. Skróć ją bez utraty głównej myśli.',
  tts_numbers: 'Zapisz liczby słowami, aby lektor poprawnie je odczytał.',
  structure: 'Scenariusz potrzebuje wyraźnego otwarcia i jednej konkretnej czynności na zakończenie.',
  repetition: 'Wskazane sceny powtarzają tę samą treść.',
  arithmetic: 'Obliczenia w scenariuszu wymagają poprawki i ponownego sprawdzenia.',
  spoken_result_mismatch: 'Wynik w narracji nie zgadza się ze sprawdzonym obliczeniem.',
  spoken_unit_mismatch: 'Jednostka w narracji nie zgadza się ze sprawdzonym obliczeniem.',
  orphan_claim: 'Scenariusz zawiera twierdzenie, które nie jest powiązane z narracją.',
  request_cadence: 'Układ narracji nie zachowuje kolejności myśli podanej w zleceniu.',
}

export function productionIssueMessage(issue: ProductionBlockerIssue): string {
  if (issue.message?.trim()) return issue.message
  if (issue.code === 'word_budget') {
    const counts = issue.detail.match(/(\d+)\s+(?:spoken\s+)?words;\s*(?:expected|required)\s+(\d+)-(\d+)/i)
    if (counts) return `Scenariusz ma ${counts[1]} słów, a wymagany zakres to ${counts[2]}–${counts[3]}.`
  }
  return SCRIPT_ISSUES[issue.code] ?? 'Wskazany fragment nie przeszedł kontroli jakości i wymaga poprawki.'
}

export function productionBlockerIssues(message: string): ProductionBlockerIssue[] {
  const parse = (value: unknown, depth = 0): ProductionBlockerIssue[] => {
    if (depth > 6) return []
    if (Array.isArray(value)) return value.flatMap(item => parse(item, depth + 1))
    if (typeof value === 'string') {
      try { return parse(JSON.parse(value), depth + 1) } catch { /* Try a JSON list following an error prefix. */ }
      const start = value.indexOf('[')
      if (start >= 0) {
        try { return parse(JSON.parse(value.slice(start)), depth + 1) } catch { return [] }
      }
      return []
    }
    if (!value || typeof value !== 'object') return []
    const item = value as Record<string, unknown>
    if (typeof item.code === 'string') return [{
      code: item.code,
      segment_ids: Array.isArray(item.segment_ids) ? item.segment_ids.filter((id): id is string => typeof id === 'string') : [],
      detail: typeof item.detail === 'string' ? item.detail : '',
      message: typeof item.message === 'string' ? item.message : undefined,
    }]
    return parse(item.issues ?? item.failures ?? item.message ?? item.detail, depth + 1)
  }
  return parse(message)
}

export function productionBlockerKind(message: string, step?: string | null): ProductionBlockerKind {
  if (/IMAGE_PLAN_UNSUPPORTED|requires a supported physical illustration/i.test(message)) return 'assets'
  if (/local_image_retry_paused|local_image_retry_needs_revision|Bounded local image action exhausted/.test(message)) return 'assets'
  if (/integrity|checkpoint.*(?:changed|mismatch)|hash.*mismatch|artifact.*(?:changed|manifest)|input.*drift/i.test(message)) return 'integrity'
  if (/awaiting_asset_review|required_assets_missing|required scene assets|asset_review/.test(message)) return 'assets'
  if (/attempt_limit|cost_limit|provider_call_limit|resource|out of memory|cuda|dependency|Preflight:|runtime_unavailable/i.test(message)) return 'resource'
  if (productionBlockerIssues(message).length || /script_quality_limit|Repair|practical_share|word_budget|spoken words/.test(message) || step?.startsWith('script')) return 'script'
  return 'other'
}

export function productionBlocker(message: string): string {
  let category = ''
  let detail = message
  try {
    const parsed: unknown = JSON.parse(message)
    if (typeof parsed === 'string') {
      detail = parsed
    } else if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const error = parsed as Record<string, unknown>
      if (typeof error.category === 'string') category = error.category
      if (typeof error.message === 'string') detail = error.message
    }
  } catch {
    // Plain-text blockers are still accepted.
  }

  const issues = productionBlockerIssues(message)
  if (/IMAGE_PLAN_UNSUPPORTED|requires a supported physical illustration/i.test(detail)) {
    return 'Plan obrazu nie pasuje do obecnych kompozycji Atnosa. Zlecenie zatrzymało się przed analizą biblioteki i generowaniem; nie powstała grafika do zatwierdzenia. Trzeba dopasować scenę do obsługiwanej ilustracji albo dodać dla niej natywną kompozycję Remotion.'
  }
  if (/local_image_retry_needs_revision/.test(detail)) return 'Poprzednia ilustracja nie przeszła oceny. Sama zmiana losowego seeda nie jest poprawką; najpierw dopracuj opis sceny lub warunkowanie obrazu na podstawie zapisanej oceny.'
  if (/local_image_retry_paused/.test(detail)) return 'Trzy kolejne ilustracje nie przeszły oceny. Produkcja zatrzymała się przed audio i renderem. Najpierw sprawdź powody odrzucenia; nie ponawiaj tej samej generacji.'
  if (/Bounded local image action exhausted/.test(detail)) return 'Wykorzystano wszystkie nowe generacje dozwolone dla tej akcji. Wznowienie nie przywraca prób ani nie rozszerza zgody. Najpierw oceń istniejące grafiki i przygotuj konkretną poprawkę.'
  if (issues.length) return `Scenariusz nadal wymaga poprawki. ${issues.map(productionIssueMessage).join(' ')}`
  const wordCounts = detail.match(/(\d+)\s+(?:spoken\s+)?words;\s*(?:required|expected)\s+(\d+)-(\d+)/i)
  if (wordCounts) return `Scenariusz ma ${wordCounts[1]} słów, a wymagany zakres to ${wordCounts[2]}–${wordCounts[3]}. Popraw długość wskazanych scen i ponownie sprawdź tekst.`
  if (/Repair did not resolve|script_quality_limit|Not enough concrete practical instruction/.test(detail) || category === 'script_quality_limit') {
    return /practical|instruction/.test(detail) ? SCRIPT_ISSUES.practical_share : 'Automatyczna poprawka scenariusza nie przeszła kontroli jakości. Potrzebna jest poprawka wskazanych fragmentów.'
  }

  if (category === 'awaiting_asset_review' || detail === 'awaiting_asset_review' || detail.includes('required scene assets await review.')) {
    const detailsPath = detail.match(/Details:\s*(.+)$/)?.[1]?.trim()
    return `Wygenerowane grafiki czekają na ocenę i zatwierdzenie. ${detailsPath ? `Szczegóły: ${detailsPath}. ` : ''}Po zatwierdzeniu wznów produkcję.`
  }
  if (category === 'required_assets_missing' || detail === 'required_assets_missing' || detail.includes('required scene assets are unavailable.')) {
    const detailsPath = detail.match(/Details:\s*(.+)$/)?.[1]?.trim()
    return `Brakuje gotowych grafik do scen. Sprawdź materiały odrzucone lub wymagające poprawy w panelu grafik. ${detailsPath ? `Szczegóły: ${detailsPath}. ` : ''}Wznów produkcję po uzupełnieniu i zatwierdzeniu zasobów.`
  }
  if (detail.startsWith('Preflight: FFmpeg or independent Whisper')) return 'Atnos nie ma dostępu do narzędzia montażu lub rozpoznawania mowy. Sprawdź FFmpeg i Whisper w ustawieniach, a następnie wznów tę produkcję.'
  if (detail.startsWith('Preflight: ElevenLabs')) return 'Lektor ElevenLabs nie jest skonfigurowany. Sprawdź klucz API i wybrany głos w ustawieniach.'
  if (detail.startsWith('Preflight: missing local models:')) return `Brakuje lokalnych modeli: ${detail.split('models:')[1]?.trim()}. Uzupełnij je w ustawieniach Ollamy.`
  if (detail.startsWith('Preflight: a configured reference/asset')) return 'Brakuje materiałów lub wzorca jakości przypisanego do kanału. Sprawdź bibliotekę i ścieżki w konfiguracji produkcji.'
  if (category === 'attempt_limit' || detail === 'attempt_limit') return 'Wyczerpano limit prób naprawy tego etapu. Przed kolejnym uruchomieniem trzeba usunąć przyczynę błędu.'
  if (category === 'cost_limit' || detail === 'cost_limit') return 'Osiągnięto limit kosztów zapisany dla tej produkcji.'
  if (category === 'provider_call_limit' || detail === 'provider_call_limit') return 'Osiągnięto limit wywołań modeli zapisany dla tej produkcji.'
  if (productionBlockerKind(message) === 'integrity') return 'Pliki lub zapis kontroli tej produkcji nie zgadzają się z zatwierdzonym stanem. Trzeba sprawdzić ich spójność przed wznowieniem.'
  if (/out of memory|cuda.*memory/i.test(detail)) return 'Brakuje wolnej pamięci GPU. Poczekaj na zwolnienie zasobów, a następnie wznów produkcję.'
  if (category === 'invalid_output' && /Repair|script|scenariusz/.test(detail)) return 'Poprawka scenariusza nie spełnia wymagań kontroli jakości. Sprawdź wskazane fragmenty tekstu.'
  return detail
}
