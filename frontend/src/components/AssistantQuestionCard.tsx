import { motion, useReducedMotion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { Check } from 'lucide-react'

export interface QuestionOption {
  id: string
  label: string
  description?: string
}

export interface AssistantQuestion {
  questionId: string
  prompt: string
  options: QuestionOption[]
  multiSelect?: boolean
  required?: boolean
  helperText?: string
}

interface AssistantQuestionCardProps {
  question: AssistantQuestion
  onSubmit: (selectedIds: string[]) => void
  disabled?: boolean
  pending?: boolean
  errorMessage?: string | null
  onRetry?: () => void
  submittedSelectedIds?: string[]
  /** When true, card renders in resolved/read-only state (after user answered) */
  resolvedSelectedIds?: string[]
}

const LETTER_FOR_INDEX = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] as const

export function AssistantQuestionCard({
  question,
  onSubmit,
  disabled = false,
  pending = false,
  errorMessage = null,
  onRetry,
  submittedSelectedIds,
  resolvedSelectedIds,
}: AssistantQuestionCardProps) {
  const reduce = useReducedMotion()
  const isResolved = resolvedSelectedIds != null && resolvedSelectedIds.length > 0
  const displaySelectedIds = useMemo(
    () => resolvedSelectedIds ?? submittedSelectedIds ?? [],
    [resolvedSelectedIds, submittedSelectedIds],
  )
  const displaySelectedKey = displaySelectedIds.join('\u0000')
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(displaySelectedIds),
  )
  const multiSelect = Boolean(question.multiSelect)
  const controlsDisabled = disabled || pending || isResolved

  useEffect(() => {
    if (displaySelectedKey) {
      setSelected(new Set(displaySelectedKey.split('\u0000')))
    }
  }, [displaySelectedKey])

  function handleToggle(optionId: string) {
    if (controlsDisabled) {
      return
    }
    setSelected((current) => {
      const next = new Set(current)
      if (multiSelect) {
        if (next.has(optionId)) {
          next.delete(optionId)
        } else {
          next.add(optionId)
        }
        return next
      }
      const auto = new Set([optionId])
      window.setTimeout(() => onSubmit([optionId]), 0)
      return auto
    })
  }

  function handleSubmit() {
    if (controlsDisabled) {
      return
    }
    if (selected.size === 0 && question.required !== false) {
      return
    }
    onSubmit(Array.from(selected))
  }

  return (
    <motion.section
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: [0, 0, 0.2, 1] }}
      role="group"
      aria-label={`Pytanie wyboru: ${question.prompt}`}
      aria-busy={pending}
      className="rounded-lg border border-border-default bg-panel/80 backdrop-blur-sm shadow-glow-soft"
    >
      <header className="border-b border-border-subtle px-5 pt-4 pb-3">
        <div className="label-eyebrow text-text-tertiary mb-2">PYTANIE</div>
        <p className="text-[15px] leading-6 text-text-primary">{question.prompt}</p>
        {question.helperText ? (
          <p className="mt-2 text-[13px] leading-5 text-text-secondary">{question.helperText}</p>
        ) : null}
      </header>

      <ul className="flex flex-col gap-2 px-3 py-3" role={multiSelect ? 'group' : 'radiogroup'}>
        {question.options.map((option, index) => {
          const isSelected = selected.has(option.id)
          const letter = LETTER_FOR_INDEX[index] ?? String(index + 1)
          return (
            <motion.li
              key={option.id}
              initial={reduce ? false : { opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.24, delay: reduce ? 0 : 0.06 * index, ease: [0, 0, 0.2, 1] }}
            >
              <button
                type="button"
                role={multiSelect ? 'checkbox' : 'radio'}
                aria-checked={isSelected}
                aria-disabled={controlsDisabled}
                disabled={controlsDisabled}
                onClick={() => handleToggle(option.id)}
                className={[
                  'group flex w-full items-start gap-3 rounded-md border px-4 py-3 text-left transition',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-primary focus-visible:outline-offset-2',
                  isSelected
                    ? 'border-accent-primary bg-accent-primary/8 shadow-glow-soft'
                    : 'border-border-default bg-elevated/50 hover:border-accent-primary/60 hover:bg-elevated',
                  controlsDisabled
                    ? 'opacity-70 cursor-not-allowed hover:border-border-default'
                    : 'cursor-pointer',
                ].join(' ')}
              >
                <span
                  aria-hidden="true"
                  className={[
                    'flex h-7 w-7 shrink-0 items-center justify-center rounded-sm border font-mono text-[13px]',
                    isSelected
                      ? 'border-accent-primary bg-accent-primary/15 text-accent-primary'
                      : 'border-border-default bg-bg-shell text-text-secondary group-hover:border-accent-primary/50 group-hover:text-text-primary',
                  ].join(' ')}
                >
                  {isResolved && isSelected ? <Check size={14} strokeWidth={2} /> : letter}
                </span>
                <span className="flex-1">
                  <span
                    className={[
                      'block text-[14px] font-medium leading-5',
                      isSelected ? 'text-text-primary' : 'text-text-primary',
                    ].join(' ')}
                  >
                    {option.label}
                  </span>
                  {option.description ? (
                    <span className="mt-1 block text-[12.5px] leading-5 text-text-secondary">
                      {option.description}
                    </span>
                  ) : null}
                </span>
              </button>
            </motion.li>
          )
        })}
      </ul>

      {multiSelect && !isResolved ? (
        <footer className="flex items-center justify-between border-t border-border-subtle px-5 py-3">
          <span className="font-mono text-[12px] text-text-tertiary">
            {pending
              ? 'Zapisywanie wyboru...'
              : selected.size === 0
                ? 'Wybierz dowolnie wiele'
                : `Zaznaczone: ${selected.size}`}
          </span>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={controlsDisabled || (selected.size === 0 && question.required !== false)}
            className={[
              'rounded-md border px-4 py-2 text-[13px] font-medium transition',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-primary focus-visible:outline-offset-2',
              selected.size > 0
                ? 'border-accent-primary bg-accent-primary/10 text-accent-primary hover:shadow-glow-medium'
                : 'border-border-default bg-elevated text-text-tertiary cursor-not-allowed',
            ].join(' ')}
          >
            Potwierdz wybor
          </button>
        </footer>
      ) : null}

      {pending && !multiSelect && !isResolved ? (
        <footer className="border-t border-border-subtle px-5 py-2.5 font-mono text-[11.5px] text-text-tertiary">
          ZAPISYWANIE WYBORU...
        </footer>
      ) : null}

      {errorMessage && !pending && !isResolved ? (
        <footer className="flex items-center justify-between gap-3 border-t border-border-subtle px-5 py-3">
          <span className="text-[12.5px] leading-5 text-sem-danger">
            {errorMessage}
          </span>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md border border-border-default bg-elevated px-3 py-1.5 text-[12px] font-medium text-text-primary transition hover:border-accent-primary/60"
            >
              Ponow
            </button>
          ) : null}
        </footer>
      ) : null}

      {isResolved ? (
        <footer className="border-t border-border-subtle px-5 py-2.5 font-mono text-[11.5px] text-text-tertiary">
          ODPOWIEDZ ZAREJESTROWANA
        </footer>
      ) : null}
    </motion.section>
  )
}
