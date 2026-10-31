import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, extname, basename } from 'node:path'

const ROOT = new URL('..', import.meta.url)
const ROOT_PATH = fileURLToPath(ROOT)

const TOKENS_PATH = join(ROOT_PATH, 'src', 'styles', 'tokens.css')
const TAILWIND_PATH = join(ROOT_PATH, 'tailwind.config.ts')
const DOC_PATH = join(ROOT_PATH, 'docs', 'design-tokens.md')
const TARGET_DIRS = [
  join(ROOT_PATH, 'src', 'components'),
  join(ROOT_PATH, 'src', 'routes'),
  join(ROOT_PATH, 'src', 'styles'),
]
const ALLOWED_LITERAL_VALUE_FILES = new Set([
  join(ROOT_PATH, 'src', 'styles', 'global.css'),
])
const IGNORED_PATH_SEGMENTS = ['/src/components/okropek/', '/src/components/OkropekCompanion.tsx']
const SHELL_COMPONENT_FILES = [
  join(ROOT_PATH, 'src', 'components', 'StatusHUD.tsx'),
  join(ROOT_PATH, 'src', 'components', 'Sidebar.tsx'),
  join(ROOT_PATH, 'src', 'components', 'CommandPalette.tsx'),
  join(ROOT_PATH, 'src', 'components', 'ApprovalGate.tsx'),
  join(ROOT_PATH, 'src', 'components', 'Toast.tsx'),
  join(ROOT_PATH, 'src', 'components', 'kbd.tsx'),
  join(ROOT_PATH, 'src', 'routes', 'Pipeline.tsx'),
  join(ROOT_PATH, 'src', 'routes', 'Telemetry.tsx'),
]
const SHELL_SPACING_FILES = [
  ...SHELL_COMPONENT_FILES,
  join(ROOT_PATH, 'src', 'routes', 'Settings.tsx'),
]

const REQUIRED_TOKENS = [
  '--bg-void',
  '--bg-shell',
  '--bg-panel',
  '--bg-elevated',
  '--surface-live',
  '--scrim-sidebar-drawer',
  '--accent-primary',
  '--accent-secondary',
  '--sem-success',
  '--sem-warning',
  '--sem-error',
  '--text-primary',
  '--text-secondary',
  '--text-tertiary',
  '--text-disabled',
  '--border-strong',
  '--border-default',
  '--border-subtle',
  '--glow-soft',
  '--glow-medium',
  '--glow-hard',
  '--glow-extreme',
  '--glow-success',
  '--glow-error',
  '--font-display',
  '--font-body',
  '--font-mono',
  '--space-1',
  '--space-2',
  '--space-3',
  '--space-4',
  '--space-6',
  '--space-8',
  '--space-12',
  '--space-16',
  '--border-w',
  '--z-page',
  '--z-hud',
  '--z-sidebar-drawer',
  '--z-pipeline-sheet',
  '--z-approval-gate',
  '--z-toast',
  '--z-command-palette',
  '--z-modal',
]
const REQUIRED_DOC_SNIPPETS = [
  'accent-primary',
  'accent-secondary',
  'gradient',
  'glow',
  'spacing',
]
const REQUIRED_TAILWIND_REFERENCES = [
  'var(--bg-void)',
  'var(--bg-shell)',
  'var(--bg-panel)',
  'var(--bg-elevated)',
  'var(--accent-primary)',
  'var(--accent-secondary)',
  'var(--sem-success)',
  'var(--sem-warning)',
  'var(--sem-error)',
  'var(--text-primary)',
  'var(--text-secondary)',
  'var(--text-tertiary)',
  'var(--text-disabled)',
  'var(--border-strong)',
  'var(--border-default)',
  'var(--border-subtle)',
  'var(--glow-soft)',
  'var(--glow-medium)',
  'var(--glow-hard)',
  'var(--glow-extreme)',
  'var(--glow-success)',
  'var(--glow-error)',
  'var(--font-display)',
  'var(--font-body)',
  'var(--font-mono)',
  'var(--border-w)',
  'var(--radius-sm)',
  'var(--radius-md)',
  'var(--radius-lg)',
  'var(--radius-pill)',
  'var(--ease-standard)',
  'var(--ease-decelerate)',
  'var(--ease-accelerate)',
  'var(--ease-spring)',
  'var(--dur-instant)',
  'var(--dur-fast)',
  'var(--dur-base)',
  'var(--dur-slow)',
  'var(--dur-deliberate)',
  'var(--space-1)',
  'var(--space-2)',
  'var(--space-3)',
  'var(--space-4)',
  'var(--space-6)',
  'var(--space-8)',
  'var(--space-12)',
  'var(--space-16)',
  'var(--z-page)',
  'var(--z-hud)',
  'var(--z-sidebar-drawer)',
  'var(--z-pipeline-sheet)',
  'var(--z-approval-gate)',
  'var(--z-toast)',
  'var(--z-command-palette)',
  'var(--z-modal)',
]
const REQUIRED_TAILWIND_SNIPPETS = [
  'fontFamily: {',
  "display: ['var(--font-display)']",
  "body: ['var(--font-body)']",
  "mono: ['var(--font-mono)']",
  'lineHeight: {',
  'letterSpacing: {',
  'borderWidth: {',
]

const errors = []
const allowedExtensions = new Set(['.ts', '.tsx', '.css'])
const literalColorPattern = /#(?:[0-9a-fA-F]{3,8})\b|rgba?\(|hsla?\(|color-mix\(/g
const literalTailwindUnitPattern = /\b\d+(?:\.\d+)?(?:px|ms)\b/g
const arbitraryClassPattern = /\b(?:bg|text|border|shadow)-\[(?:#|rgb|hsl|var\()[^\]]+\]/g
const arbitraryTypographyPattern = /\b(?:text|leading|tracking|font)-\[[^\]]+\]/g
const zIndexPattern = /\bz-\[(\d+)\]|\bz-(\d+)\b/g
const allowedZIndexUtilities = new Set([
  'z-page',
  'z-hud',
  'z-sidebar-drawer',
  'z-pipeline-sheet',
  'z-approval',
  'z-toast',
  'z-palette',
  'z-modal',
  'z-auto',
  'z-0',
])
const offScaleSpacingPattern =
  /\b(?:p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml|gap|space-x|space-y)-\[(\d+)px\]/g
const allowedSpacing = new Set(['4', '8', '12', '16', '24', '32', '48', '64'])
const standardSpacingPattern =
  /\b(?:p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml|gap|space-x|space-y)-([0-9]+(?:\.5)?|px)\b/g
const allowedSpacingUtilities = new Set(['0', '1', '2', '3', '4', '6', '8', '12', '16', 'px'])

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      walk(fullPath)
      continue
    }
    if (!allowedExtensions.has(extname(fullPath))) {
      continue
    }
    inspectFile(fullPath)
  }
}

function inspectFile(filePath) {
  const content = readFileSync(filePath, 'utf8')
  const isTokensFile = filePath === TOKENS_PATH
  const normalizedPath = filePath.replaceAll('\\', '/')

  if (IGNORED_PATH_SEGMENTS.some((segment) => normalizedPath.includes(segment))) {
    return
  }

  if (!isTokensFile) {
    if (!ALLOWED_LITERAL_VALUE_FILES.has(filePath) && literalColorPattern.test(content)) {
      errors.push(`${filePath}: literal color/shadow value found`)
    }
    if (arbitraryClassPattern.test(content)) {
      errors.push(`${filePath}: arbitrary Tailwind color/shadow class found`)
    }
  }

  let match
  while ((match = offScaleSpacingPattern.exec(content))) {
    if (!allowedSpacing.has(match[1])) {
      errors.push(`${filePath}: off-scale spacing ${match[1]}px`)
    }
  }

  if (SHELL_SPACING_FILES.includes(filePath)) {
    while ((match = standardSpacingPattern.exec(content))) {
      if (!allowedSpacingUtilities.has(match[1])) {
        errors.push(`${filePath}: off-scale spacing utility ${match[0]}`)
      }
    }
  }

  if (SHELL_COMPONENT_FILES.includes(filePath) && arbitraryTypographyPattern.test(content)) {
    errors.push(`${filePath}: arbitrary typography value found in shell component`)
  }

  if (basename(filePath) !== 'tailwind.config.ts') {
    while ((match = zIndexPattern.exec(content))) {
      const utility = match[0]
      if (!allowedZIndexUtilities.has(utility)) {
        errors.push(`${filePath}: non-contract z-index utility ${utility}`)
      }
    }
  }
}

const tokensContent = readFileSync(TOKENS_PATH, 'utf8')
for (const token of REQUIRED_TOKENS) {
  if (!tokensContent.includes(token)) {
    errors.push(`tokens.css: missing required token ${token}`)
  }
}

const tailwindContent = readFileSync(TAILWIND_PATH, 'utf8')
if (literalColorPattern.test(tailwindContent)) {
  errors.push('tailwind.config.ts: literal color/shadow value found')
}
if (literalTailwindUnitPattern.test(tailwindContent)) {
  errors.push('tailwind.config.ts: literal px/ms value found in tokenized area')
}
for (const reference of REQUIRED_TAILWIND_REFERENCES) {
  if (!tailwindContent.includes(reference)) {
    errors.push(`tailwind.config.ts: missing token mapping for ${reference}`)
  }
}
for (const snippet of REQUIRED_TAILWIND_SNIPPETS) {
  if (!tailwindContent.includes(snippet)) {
    errors.push(`tailwind.config.ts: missing required section ${snippet}`)
  }
}

const docContent = readFileSync(DOC_PATH, 'utf8')
for (const snippet of REQUIRED_DOC_SNIPPETS) {
  if (!docContent.includes(snippet)) {
    errors.push(`docs/design-tokens.md: missing required guidance for ${snippet}`)
  }
}

for (const dir of TARGET_DIRS) {
  walk(dir)
}

if (errors.length > 0) {
  console.error('Design token verification failed:')
  for (const error of errors) {
    console.error(`- ${error}`)
  }
  process.exit(1)
}

console.log('Design token verification passed.')
