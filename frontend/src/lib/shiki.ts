const SHIKI_LANGS = [
  'typescript',
  'tsx',
  'javascript',
  'jsx',
  'json',
  'python',
  'bash',
  'diff',
  'markdown',
  'yaml',
  'css',
  'html',
] as const

type ShikiHighlighter = Awaited<
  ReturnType<(typeof import('shiki'))['createHighlighter']>
>

let shikiHighlighterPromise: Promise<ShikiHighlighter> | null = null

function resolveColorToken(token: string, fallback: string) {
  if (typeof window === 'undefined') {
    return fallback
  }

  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(token)
      .trim() || fallback
  )
}

function createAtnosShikiTheme() {
  return {
    name: 'atnos-shell',
    type: 'dark' as const,
    colors: {
      'editor.background': resolveColorToken('--code-theme-bg', '#11141B'),
      'editor.foreground': resolveColorToken('--code-theme-foreground', '#EDEAE0'),
    },
    tokenColors: [
      {
        scope: ['comment', 'punctuation.definition.comment'],
        settings: {
          foreground: resolveColorToken('--code-theme-comment', '#5C5E63'),
          fontStyle: 'italic',
        },
      },
      {
        scope: ['keyword', 'storage', 'entity.name.tag'],
        settings: {
          foreground: resolveColorToken('--code-theme-keyword', '#C2E5FF'),
        },
      },
      {
        scope: ['string', 'string.quoted', 'constant.other.symbol'],
        settings: {
          foreground: resolveColorToken('--code-theme-string', '#FF5B2ECC'),
        },
      },
      {
        scope: ['constant.numeric', 'constant.language', 'support.constant'],
        settings: {
          foreground: resolveColorToken('--accent-primary', '#C2E5FF'),
        },
      },
      {
        scope: ['entity.name.function', 'support.function', 'variable.function'],
        settings: {
          foreground: resolveColorToken('--text-primary', '#EDEAE0'),
        },
      },
      {
        scope: ['variable', 'meta.object-literal.key', 'entity.name.type'],
        settings: {
          foreground: resolveColorToken('--code-theme-foreground', '#EDEAE0'),
        },
      },
    ],
  }
}

export function getShikiLanguage(language: string) {
  const normalized = language.trim().toLowerCase()
  if (normalized === 'js') return 'javascript'
  if (normalized === 'ts') return 'typescript'
  if (normalized === 'py') return 'python'
  if (normalized === 'yml') return 'yaml'
  if (normalized === 'sh' || normalized === 'shell' || normalized === 'zsh') {
    return 'bash'
  }
  if ((SHIKI_LANGS as readonly string[]).includes(normalized)) {
    return normalized
  }
  return null
}

export async function getShikiHighlighter() {
  if (!shikiHighlighterPromise) {
    shikiHighlighterPromise = import('shiki').then(({ createHighlighter }) =>
      createHighlighter({
        themes: [createAtnosShikiTheme()],
        langs: [...SHIKI_LANGS],
      }),
    )
  }

  return shikiHighlighterPromise
}
