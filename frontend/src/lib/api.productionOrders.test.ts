import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authorizeProductionCallLimit, createProductionOrder, fetchProductionImageOptions, fetchProductionOrderRecovery, repairProductionOrderScript } from './api'

describe('Production order HTTP contract', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('sends the chosen local candidate, zero-paid policy and explicit duration', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"prod-local"}', { status: 202 }))
    await createProductionOrder({ topic: 'Local voice test', voiceCandidate: 'Ryan-B', providerPolicy: 'strict_local', targetDurationSeconds: 30, deliveryMode: 'local_only' })
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toMatchObject({
      voice_candidate: 'Ryan-B', provider_policy: 'strict_local', target_duration_seconds: 30, delivery_mode: 'local_only',
    })
  })

  it('sends the selected versioned WB recipe and neutral Ryan without image-provider fields', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"recipe-order"}', {status:202}))
    await createProductionOrder({topic:'Two claims',productionSetId:'wb-editorial-v1',voiceCandidate:'Ryan-neutral',providerPolicy:'strict_local',deliveryMode:'local_only'})
    const body=JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))
    expect(body).toMatchObject({production_set_id:'wb-editorial-v1',voice_candidate:'Ryan-neutral',provider_policy:'strict_local'})
    expect(body).not.toHaveProperty('image_provider');expect(body).not.toHaveProperty('visual_style_id')
  })

  it('keeps JSON content type when an order has an idempotency key', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"prod-test"}', { status: 202 }))
    await createProductionOrder({ topic: 'Savings habit', requestKey: 'workspace-test', deliveryMode: 'local_only' })
    const [url, request] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(request?.headers)
    expect(url).toEqual(expect.stringContaining('/production/orders'))
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Idempotency-Key')).toBe('workspace-test')
    expect(JSON.parse(String(request?.body))).toMatchObject({ project: 'wealth_blueprint', delivery_mode: 'local_only' })
  })

  it('sends only the explicitly selected local image provider, reference, and style', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"prod-test"}', { status: 202 }))
    await createProductionOrder({ topic: 'Savings habit', project: 'wealth_blueprint', deliveryMode: 'local_only',
      imageProvider: 'comfyui-local', imageReferenceId: 'wb-original-board', visualStyleId: 'C' })
    const [, request] = vi.mocked(fetch).mock.calls[0]
    expect(JSON.parse(String(request?.body))).toMatchObject({
      project: 'wealth_blueprint', delivery_mode: 'local_only', image_provider: 'comfyui-local',
      image_reference_id: 'wb-original-board', visual_style_id: 'C',
    })
    expect(JSON.parse(String(request?.body))).not.toHaveProperty('voice')
    expect(JSON.parse(String(request?.body))).not.toHaveProperty('voice_id')
  })

  it('reads public image provider, reference availability, and style options', async () => {
    const response = {
      providers: [{ id: 'comfyui-local', label: 'ComfyUI lokalnie', available: true, requires_reference: true }],
      references: [{ id: 'wb-original-board', label: 'Plansza referencyjna', available: false, reason: 'missing' }],
      styles: [
        { id: 'A', name: 'Ilustracja redakcyjna' },
        { id: 'B', name: 'Warstwy papieru' },
        { id: 'C', name: 'Rysunek techniczny' },
        { id: 'D', name: 'Kadr filmowy' },
      ],
    }
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }))
    expect(await fetchProductionImageOptions()).toEqual(response)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/production/image-options'), expect.any(Object))
  })

  it('rejects a malformed successful image-options response with a readable error', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await expect(fetchProductionImageOptions()).rejects.toThrow('Odpowiedź opcji grafiki z API ma nieprawidłowy format.')
  })

  it('renders validation messages without object coercion or submitted input', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: [
      { loc: ['body', 'topic'], msg: 'Temat musi mieć co najmniej trzy znaki.', input: 'PRIVATE_INPUT' },
    ] }), { status: 422 }))
    await expect(createProductionOrder({ topic: 'x' })).rejects.toThrow('Temat musi mieć co najmniej trzy znaki.')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('sends only the explicitly selected call limit as JSON without requesting a worker', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"prod-test","max_provider_calls":45}', { status: 200 }))
    await authorizeProductionCallLimit('prod-test', 45)
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, request] = vi.mocked(fetch).mock.calls[0]
    expect(url).toEqual(expect.stringContaining('/production/orders/prod-test/call-limit'))
    expect(request?.method).toBe('POST')
    expect(new Headers(request?.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(request?.body))).toEqual({ max_calls: 45 })
  })

  it('sends a scoped repair note and stable request key to the real order repair endpoint', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"id":"prod-test","status":"running"}', { status: 202 }))
    await repairProductionOrderScript('prod/test', { note: 'Dodaj praktyczną czynność.', requestKey: 'repair-request-123' })
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, request] = vi.mocked(fetch).mock.calls[0]
    expect(url).toEqual(expect.stringContaining('/production/orders/prod%2Ftest/script-repair'))
    expect(request?.method).toBe('POST')
    expect(JSON.parse(String(request?.body))).toEqual({ note: 'Dodaj praktyczną czynność.', request_key: 'repair-request-123' })
    expect(new Headers(request?.headers).get('Content-Type')).toBe('application/json')
  })

  it('treats an unavailable recovery endpoint as no repair capability and preserves other errors', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{"detail":"Not Found"}', { status: 404 }))
    expect(await fetchProductionOrderRecovery('prod-test')).toBeNull()
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{"detail":"Sprawdź spójność plików."}', { status: 409 }))
    await expect(fetchProductionOrderRecovery('prod-test')).rejects.toThrow('Sprawdź spójność plików.')
  })
})
