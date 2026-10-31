import { readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT_PATH = fileURLToPath(new URL('..', import.meta.url))
const TARGET_DIRS = [
  join(ROOT_PATH, 'src'),
]
const ALLOWED_EXTENSIONS = new Set(['.ts', '.tsx', '.css', '.md'])

// Typical UTF-8 mojibake markers seen when Polish text is decoded as a legacy Windows encoding.
const SUSPECT_CODEPOINTS = new Map([
  [0x00c2, 'Â'],
  [0x00c3, 'Ã'],
  [0x00c4, 'Ä'],
  [0x00c5, 'Å'],
  [0x0102, 'Ă'],
  [0x0139, 'Ĺ'],
  [0xfffd, '�'],
])

const errors = []

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === 'dist' || entry === 'coverage') continue
      walk(fullPath)
      continue
    }
    if (!ALLOWED_EXTENSIONS.has(extname(fullPath))) continue
    inspectFile(fullPath)
  }
}

function inspectFile(filePath) {
  const content = readFileSync(filePath, 'utf8')
  const lines = content.split(/\r?\n/)
  for (const [index, line] of lines.entries()) {
    for (const char of line) {
      const codePoint = char.codePointAt(0)
      if (codePoint == null || !SUSPECT_CODEPOINTS.has(codePoint)) continue
      errors.push({
        file: relative(ROOT_PATH, filePath),
        line: index + 1,
        char: SUSPECT_CODEPOINTS.get(codePoint),
        text: line.trim(),
      })
      break
    }
  }
}

for (const dir of TARGET_DIRS) {
  walk(dir)
}

if (errors.length > 0) {
  console.error('Polish text encoding verification failed:')
  for (const error of errors.slice(0, 80)) {
    console.error(`- ${error.file}:${error.line}: suspect "${error.char}" in ${JSON.stringify(error.text)}`)
  }
  if (errors.length > 80) {
    console.error(`- ...and ${errors.length - 80} more suspect lines`)
  }
  process.exit(1)
}

console.log('Polish text encoding verification passed.')
