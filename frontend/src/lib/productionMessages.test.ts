import { describe, expect, it } from 'vitest'

import { productionBlocker, productionBlockerIssues, productionBlockerKind, productionStepLabel, productionSubmissionBlocker } from './productionMessages'

describe('free production constraint language', () => {
  it.each(['Zrób tę rolkę bezpłatnie.', 'Wyłącznie darmowe narzędzia.', 'Budżet 0 USD.',
    'Budget $0.', 'Use free tools only.', 'No paid services.', 'Bez płatnych usług.', 'Bez kosztów API.'])('protects %s until local voice selection', text => {
    expect(productionSubmissionBlocker(text)).toContain('Ryana B')
    expect(productionSubmissionBlocker(text, true)).toBe('')
  })
  it.each(['Explain free cash flow.', 'Opowiedz o darmowych próbkach.', 'Stocks with a zero-cost product.', 'Budget $100.', 'Budget $0.50.', 'Budżet 0,25 USD.'])('does not mistake the topic %s for a cost constraint', text => {
    expect(productionSubmissionBlocker(text)).toBe('')
  })
})

describe('production script recovery messages', () => {
  it('extracts validation issues from a prefixed JSON list inside an error envelope', () => {
    const error = JSON.stringify({ category: 'invalid_output', message: `Repair did not resolve the flagged validation errors: ${JSON.stringify([
      { code: 'practical_share', segment_ids: ['s02', 's03'], detail: 'Not enough concrete practical instruction' },
      { code: 'word_budget', segment_ids: ['s01'], detail: '127 words; expected 90-125' },
    ])}` })
    expect(productionBlockerIssues(error)).toHaveLength(2)
    expect(productionBlockerIssues(error)[0].segment_ids).toEqual(['s02', 's03'])
    const text = productionBlocker(error)
    expect(text).toContain('Za mało konkretnych działań dla widza.')
    expect(text).toContain('127 słów, a wymagany zakres to 90–125')
    expect(text).not.toContain('Repair did not resolve')
    expect(text).not.toContain('"segment_ids"')
  })

  it('explains word allocation failures without exposing the per-segment dictionary', () => {
    const text = productionBlocker(JSON.stringify({ category: 'invalid_output', message: "Repair still has 127 spoken words; required 90-125. Use the provided per-segment word targets: {'s01': 20, 's02': 22}" }))
    expect(text).toContain('Scenariusz ma 127 słów')
    expect(text).toContain('90–125')
    expect(text).not.toContain('word targets')
  })

  it.each([
    ['script_quality_limit', 'script_repair/1', 'script'],
    ['awaiting_asset_review', 'asset_review', 'assets'],
    ['provider_call_limit', 'script_repair/1', 'resource'],
    ['CUDA out of memory', 'script_draft', 'resource'],
    ['Checkpoint hash mismatch', 'script_repair/1', 'integrity'],
  ])('classifies %s without confusing it with the script step', (error, step, kind) => {
    expect(productionBlockerKind(error, step)).toBe(kind)
  })

  it('accepts malformed details without throwing or inventing a passed QA result', () => {
    expect(productionBlockerIssues('Repair did not resolve: [broken')).toEqual([])
    expect(productionBlocker('Repair did not resolve: [broken')).toContain('nie przeszła kontroli jakości')
  })
})

describe('production asset review messages', () => {
  it('explains unsupported image plans as a pre-generation blocker instead of asking for nonexistent review', () => {
    const error = JSON.stringify({ category: 'execution_failure', message:
      'IMAGE_PLAN_UNSUPPORTED: Image plan requires a supported physical illustration for every scene; no image was generated. Unsupported scenes: s01 (asset type: icon).' })
    expect(productionBlockerKind(error, 'asset_planning')).toBe('assets')
    expect(productionBlocker(error)).toContain('nie powstała grafika do zatwierdzenia')
    expect(productionBlocker(error)).toContain('natywną kompozycję Remotion')
  })

  it('explains unavailable assets without suggesting approval can fix technical failures', () => {
    const path = 'E:\\Atnos\\data\\scene_production_assets.json'
    const detail = `4 required scene assets are unavailable. Details: ${path}`
    const text = productionBlocker(JSON.stringify({ category: 'required_assets_missing', message: detail }))
    expect(text).toContain('Brakuje gotowych grafik do scen.')
    expect(text).toContain('odrzucone lub wymagające poprawy')
    expect(text).toContain(path)
    expect(text).not.toContain('czekają na ocenę i zatwierdzenie')
    expect(productionBlocker(detail)).toBe(text)
  })

  it('distinguishes asset review from asset planning', () => {
    expect(productionStepLabel('asset_review')).toBe('Grafiki czekają na ocenę i zatwierdzenie')
    expect(productionStepLabel('asset_planning')).toBe('Dobieranie scen i materiałów')
    expect(productionStepLabel('assets/image_review/abc/comparison')).toBe('Automatyczne sprawdzanie wygenerowanej grafiki')
    expect(productionStepLabel('assets/image_local/s01/abc')).toBe('Generowanie ilustracji w ComfyUI')
    expect(productionStepLabel('remotion_render')).toBe('Montaż i renderowanie')
  })

  it('explains the review hold in Polish and keeps the artifact location', () => {
    const path = 'E:\\Atnos\\App\\data\\production\\scene_production_assets.json'
    const blocker = productionBlocker(`2 required scene assets await review. Details: ${path}`)
    expect(blocker).toContain('Wygenerowane grafiki czekają na ocenę i zatwierdzenie.')
    expect(blocker).toContain(path)
    expect(blocker).toContain('Po zatwierdzeniu wznów produkcję.')
    expect(productionBlocker('awaiting_asset_review')).toContain('czekają na ocenę i zatwierdzenie')

    const backendError = JSON.stringify({
      category: 'awaiting_asset_review',
      message: `2 required scene assets await review. Details: ${path}`,
    })
    const fromJson = productionBlocker(backendError)
    expect(fromJson).toBe(blocker)
    expect(fromJson).not.toContain('"category"')
    expect(fromJson).not.toContain('"}')
  })
})
