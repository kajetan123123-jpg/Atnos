const fs = await import('node:fs/promises')
const path = await import('node:path')

const DEFAULT_ACTION_TIMEOUT_MS = 15000
const DEFAULT_OVERLAY_TIMEOUT_MS = 3000

async function main() {
  const specPath = process.argv[2]
  if (!specPath) {
    writeFailure('Missing spec path.', { code: 'missing_spec' })
    process.exit(1)
  }

  const spec = JSON.parse(await fs.readFile(specPath, 'utf-8'))
  const { chromium } = await import('playwright')
  const context = await chromium.launchPersistentContext(
    String(spec.profileDir ?? path.join(path.dirname(specPath), 'profile')),
    {
      headless: spec.headless === true,
      executablePath: spec.browserExecutable ? String(spec.browserExecutable) : undefined,
      channel: spec.browserExecutable ? undefined : process.platform === 'win32' ? 'msedge' : undefined,
      acceptDownloads: true,
      downloadsPath: spec.downloadsDir ? String(spec.downloadsDir) : undefined,
    },
  )
  if (spec.tracePath) {
    await context.tracing.start({ screenshots: true, snapshots: true })
  }

  const page = context.pages()[0] ?? (await context.newPage())
  const actionTimeoutMs = Number(spec.actionTimeoutMs ?? DEFAULT_ACTION_TIMEOUT_MS)
  const overlayProbeTimeoutMs = Number(spec.overlayProbeTimeoutMs ?? DEFAULT_OVERLAY_TIMEOUT_MS)
  const retryLimit = Number(spec.retryLimit ?? 2)
  page.setDefaultTimeout(actionTimeoutMs)
  page.setDefaultNavigationTimeout(actionTimeoutMs)

  const steps = Array.isArray(spec.steps) ? spec.steps : []
  const artifacts = {}
  const results = []

  try {
    for (const [index, step] of steps.entries()) {
      const stepResult = await executeStepWithRetry({
        page,
        step,
        index,
        retryLimit,
        actionTimeoutMs,
        overlayProbeTimeoutMs,
        cookieSelectors: Array.isArray(spec.cookieSelectors) ? spec.cookieSelectors : [],
        modalCloseSelectors: Array.isArray(spec.modalCloseSelectors) ? spec.modalCloseSelectors : [],
        downloadsDir: spec.downloadsDir ? String(spec.downloadsDir) : path.dirname(specPath),
        artifacts,
        specDir: path.dirname(specPath),
      })
      results.push(stepResult)
    }

    const finalShot = spec.finalScreenshotPath
      ? String(spec.finalScreenshotPath)
      : path.join(path.dirname(specPath), 'trusted-auto-final.png')
    await page.screenshot({ path: finalShot, fullPage: true })
    artifacts.final_screenshot = finalShot
    if (spec.tracePath) {
      await context.tracing.stop({ path: String(spec.tracePath) })
      artifacts.trace = String(spec.tracePath)
    }
    console.log(JSON.stringify({ ok: true, results, finalUrl: page.url(), artifacts }))
  } catch (error) {
    const code = resolveErrorCode(error)
    const screenshotPath = spec.errorScreenshotPath
      ? String(spec.errorScreenshotPath)
      : path.join(path.dirname(specPath), 'trusted-auto-error.png')
    try {
      await page.screenshot({ path: screenshotPath, fullPage: true })
      artifacts.error_screenshot = screenshotPath
    } catch {}
    if (spec.tracePath) {
      try {
        await context.tracing.stop({ path: String(spec.tracePath) })
        artifacts.trace = String(spec.tracePath)
      } catch {}
    }
    writeFailure(error instanceof Error ? error.message : String(error), {
      code,
      results,
      finalUrl: safePageUrl(page),
      artifacts,
    })
    process.exitCode = 1
  } finally {
    await context.close()
  }
}

async function executeStepWithRetry({
  page,
  step,
  index,
  retryLimit,
  actionTimeoutMs,
  overlayProbeTimeoutMs,
  cookieSelectors,
  modalCloseSelectors,
  downloadsDir,
  artifacts,
  specDir,
}) {
  const type = String(step.type ?? '')
  let attempt = 0
  while (true) {
    try {
      const result = await executeSingleStep({
        page,
        step,
        index,
        actionTimeoutMs,
        overlayProbeTimeoutMs,
        cookieSelectors,
        modalCloseSelectors,
        downloadsDir,
        artifacts,
        specDir,
      })
      result.attempt = attempt + 1
      return result
    } catch (error) {
      const errorCode = resolveErrorCode(error)
      const retryable = errorCode === 'request_timeout' || errorCode === 'runtime_busy'
      if (!retryable || attempt >= retryLimit) {
        throw error
      }
      if (errorCode === 'runtime_busy') {
        await page.waitForTimeout(2000)
      }
      attempt += 1
    }
  }
}

async function executeSingleStep({
  page,
  step,
  index,
  actionTimeoutMs,
  overlayProbeTimeoutMs,
  cookieSelectors,
  modalCloseSelectors,
  downloadsDir,
  artifacts,
  specDir,
}) {
  const type = String(step.type ?? '')
  const stepResult = { stepIndex: index, type, ok: true }

  if (type === 'navigate') {
    await page.goto(String(step.url), {
      waitUntil: 'load',
      timeout: Number(step.timeoutMs ?? actionTimeoutMs),
    })
    await autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs)
    stepResult.url = page.url()
    return stepResult
  }

  if (type === 'wait') {
    await page.waitForTimeout(Number(step.ms ?? 1000))
    return stepResult
  }

  if (type === 'click') {
    await autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs)
    const locator = await resolveLocator(page, step)
    try {
      await locator.click({ timeout: Number(step.timeoutMs ?? actionTimeoutMs) })
    } catch (error) {
      const blocking = await detectBlockingOverlay(page)
      if (blocking) {
        throw createCodedError(`Browser action blocked by overlay: ${blocking}`, 'blocked_by_overlay')
      }
      throw error
    }
    return stepResult
  }

  if (type === 'type') {
    await autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs)
    const locator = await resolveLocator(page, step)
    try {
      await locator.fill(String(step.text ?? ''), { timeout: Number(step.timeoutMs ?? actionTimeoutMs) })
    } catch (error) {
      const blocking = await detectBlockingOverlay(page)
      if (blocking) {
        throw createCodedError(`Browser action blocked by overlay: ${blocking}`, 'blocked_by_overlay')
      }
      throw error
    }
    return stepResult
  }

  if (type === 'upload_file') {
    await autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs)
    const locator = await resolveLocator(page, step)
    await locator.setInputFiles(String(step.filePath), { timeout: Number(step.timeoutMs ?? actionTimeoutMs) })
    return stepResult
  }

  if (type === 'download') {
    const downloadPromise = page.waitForEvent('download', {
      timeout: Number(step.timeoutMs ?? actionTimeoutMs),
    })
    if (step.selector || step.text) {
      const locator = await resolveLocator(page, step)
      await locator.click({ timeout: Number(step.timeoutMs ?? actionTimeoutMs) })
    } else {
      await page.keyboard.press('Enter')
    }
    const download = await downloadPromise
    const suggestedName = await download.suggestedFilename()
    const requestedPath = step.path
      ? String(step.path)
      : path.join(downloadsDir, suggestedName)
    await download.saveAs(requestedPath)
    artifacts[`download_${index}`] = requestedPath
    stepResult.path = requestedPath
    return stepResult
  }

  if (type === 'extract') {
    const locator = await resolveLocator(page, step)
    if (step.attr) {
      stepResult.value = await locator.getAttribute(String(step.attr))
    } else {
      stepResult.value = await locator.textContent()
    }
    return stepResult
  }

  if (type === 'collect_links') {
    const selector = String(step.selector ?? 'a[href]')
    const links = await page.locator(selector).evaluateAll((nodes) =>
      nodes.map((node) => ({
        text: (node.textContent ?? '').trim(),
        url: node instanceof HTMLAnchorElement ? node.href : '',
      })),
    )
    stepResult.value = links
    return stepResult
  }

  if (type === 'fill_fields') {
    const rawFields = Array.isArray(step.fields) ? step.fields : []
    const filled = []
    for (const rawField of rawFields) {
      if (!rawField || typeof rawField !== 'object') continue
      const field = rawField
      const locator = await resolveFieldLocator(page, field)
      await locator.fill(String(field.value ?? ''), {
        timeout: Number(field.timeoutMs ?? step.timeoutMs ?? actionTimeoutMs),
      })
      filled.push({
        label: field.label ?? null,
        name: field.name ?? null,
        selector: field.selector ?? null,
      })
    }
    stepResult.value = filled
    return stepResult
  }

  if (type === 'submit') {
    await autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs)
    if (step.selector || step.text) {
      const locator = await resolveLocator(page, step)
      await locator.click({ timeout: Number(step.timeoutMs ?? actionTimeoutMs) })
    } else {
      await page.keyboard.press('Enter')
    }
    return stepResult
  }

  if (type === 'verify_state') {
    if (step.selector) {
      const locator = page.locator(String(step.selector)).first()
      await locator.waitFor({ timeout: Number(step.timeoutMs ?? actionTimeoutMs), state: 'visible' })
    } else if (step.text) {
      await page.getByText(String(step.text), { exact: Boolean(step.exact) }).first().waitFor({
        timeout: Number(step.timeoutMs ?? actionTimeoutMs),
        state: 'visible',
      })
    } else {
      throw createCodedError('verify_state requires selector or text.', 'invalid_recipe')
    }
    return stepResult
  }

  if (type === 'screenshot') {
    const requestedPath = step.path
      ? String(step.path)
      : path.join(specDir, `step-${index}.png`)
    await page.screenshot({ path: requestedPath, fullPage: Boolean(step.fullPage) })
    artifacts[`screenshot_${index}`] = requestedPath
    stepResult.path = requestedPath
    return stepResult
  }

  throw createCodedError(`Unsupported step type '${type}'.`, 'unsupported_step')
}

async function autoDismissOverlays(page, cookieSelectors, modalCloseSelectors, overlayProbeTimeoutMs) {
  const startedAt = Date.now()
  for (const selector of cookieSelectors) {
    if (Date.now() - startedAt >= overlayProbeTimeoutMs) break
    try {
      const locator = page.locator(String(selector)).first()
      if (await locator.isVisible({ timeout: 200 }).catch(() => false)) {
        await locator.click({ timeout: 750 })
      }
    } catch {}
  }
  for (const selector of modalCloseSelectors) {
    if (Date.now() - startedAt >= overlayProbeTimeoutMs) break
    try {
      const locator = page.locator(String(selector)).first()
      if (await locator.isVisible({ timeout: 200 }).catch(() => false)) {
        await locator.click({ timeout: 750 })
      }
    } catch {}
  }
}

async function detectBlockingOverlay(page) {
  const probeSelectors = [
    "[role='dialog']",
    "[class*='modal']",
    "[class*='overlay']",
    "[class*='cookie']",
    "[id*='cookie']",
  ]
  for (const selector of probeSelectors) {
    try {
      const locator = page.locator(selector).first()
      if (await locator.isVisible({ timeout: 250 }).catch(() => false)) {
        return selector
      }
    } catch {}
  }
  return null
}

async function resolveLocator(page, step) {
  if (step.selector) {
    return page.locator(String(step.selector)).first()
  }
  if (step.text) {
    return page.getByText(String(step.text), { exact: Boolean(step.exact) }).first()
  }
  throw createCodedError('Step requires selector or text.', 'invalid_recipe')
}

async function resolveFieldLocator(page, field) {
  if (field.selector) {
    return page.locator(String(field.selector)).first()
  }
  if (field.label) {
    const byLabel = page.getByLabel(String(field.label), { exact: false }).first()
    if (await byLabel.count()) {
      return byLabel
    }
  }
  if (field.name) {
    const byName = page.locator(`[name="${cssEscape(String(field.name))}"]`).first()
    if (await byName.count()) {
      return byName
    }
  }
  if (field.placeholder) {
    const byPlaceholder = page.getByPlaceholder(String(field.placeholder), { exact: false }).first()
    if (await byPlaceholder.count()) {
      return byPlaceholder
    }
  }
  throw createCodedError('fill_fields could not resolve one of the target fields.', 'field_not_found')
}

function resolveErrorCode(error) {
  if (error && typeof error === 'object' && 'code' in error && typeof error.code === 'string') {
    return error.code
  }
  const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase()
  if (
    message.includes('target page, context or browser has been closed') ||
    message.includes('browser has been closed') ||
    message.includes('browser closed')
  ) {
    return 'browser_crashed'
  }
  if (message.includes('timeout')) {
    return 'request_timeout'
  }
  if (message.includes('busy')) {
    return 'runtime_busy'
  }
  return 'browser_step_failed'
}

function createCodedError(message, code) {
  const error = new Error(message)
  error.code = code
  return error
}

function safePageUrl(page) {
  try {
    return page.url()
  } catch {
    return ''
  }
}

function writeFailure(message, payload = {}) {
  console.log(JSON.stringify({ ok: false, error: message, ...payload }))
}

function cssEscape(value) {
  return String(value).replaceAll('\\', '\\\\').replaceAll('"', '\\"')
}

await main()
