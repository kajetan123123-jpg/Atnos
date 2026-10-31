import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatMessage } from './ChatMessage'
import * as api from '../lib/api'
import { resetAllStores } from '../test/resetStores'
import { useChatStore } from '../stores/chatStore'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    approveActionDraft: vi.fn(),
    answerQuestion: vi.fn(),
    executeActionDraft: vi.fn(),
    rejectActionDraft: vi.fn(),
  }
})

describe('ChatMessage', () => {
  it.each([
    { status: 'not_found' }, { status: 'blocked' }, { status: 'ambiguous' },
    { status: 'incomplete' }, { command_exit_code: 1 }, { status: 'opened', error: 'host failed' },
  ])('does not mark an unsuccessful desktop launch as done: %j', (result) => {
    render(<ChatMessage message={{ id: 9904, role: 'assistant', status: 'completed',
      content: 'Nie otworzono aplikacji.', createdAt: '2026-10-29T20:23:00Z',
      metadataJson: { desktop_operator: { action: 'desktop_app_open', query: 'Notatnik', ...result } },
    }} />)
    expect(screen.queryByText('Zrobione')).not.toBeInTheDocument()
    expect(screen.queryByText('gotowe')).not.toBeInTheDocument()
  })

  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
  })

  it.each(['needs_confirmation', 'failed', 'blocked'])('does not label a %s shell operation as executed', (toolStatus) => {
    render(<ChatMessage message={{
      id: 9901, role: 'assistant', status: 'completed', content: 'Wynik bramki.',
      createdAt: '2026-10-08T18:00:00Z', metadataJson: {
        agentic_tool_loop: { status: toolStatus, tool_calls: [{ name: 'shell_powershell', status: toolStatus, summary: 'Testowe polecenie' }] },
      },
    }} />)
    expect(screen.getByTestId('agentic-tool-timeline')).toBeInTheDocument()
    expect(screen.queryByText('Wykonano polecenie')).not.toBeInTheDocument()
  })

  it('shows routed skills separately from confirmed tool execution', () => {
    render(<ChatMessage message={{
      id: 9902, role: 'assistant', status: 'completed', content: 'Znalazłem źródła.',
      createdAt: '2026-10-18T12:00:00Z', metadataJson: {
        atnos_skill_execution: {
          selectedSkills: ['atnos:deep-research'],
          loadedInstructionSkills: ['atnos:deep-research'],
          missingInstructionSkills: [],
          instructionStatus: 'loaded',
        },
      },
    }} />)

    expect(screen.getByTestId('atnos-skill-execution')).toBeInTheDocument()
    expect(screen.getByText('Skille: atnos:deep-research')).toBeInTheDocument()
    expect(screen.getByText('Wczytane: atnos:deep-research')).toBeInTheDocument()
    expect(screen.getByText(/Wybór skilla nie oznacza wykonania narzędzia/)).toBeInTheDocument()
  })

  it('keeps an incomplete browser analysis visibly unfinished', () => {
    render(<ChatMessage message={{
      id: 9903, role: 'assistant', status: 'completed', content: 'Nie ukończyłem analizy.',
      createdAt: '2026-10-18T12:00:00Z', metadataJson: {
        browser_operator: { action: 'learn_page', status: 'incomplete', target_url: 'https://example.com/' },
      },
    }} />)
    expect(screen.getByText('nieukończone')).toBeInTheDocument()
    expect(screen.queryByText('Zrobione')).not.toBeInTheDocument()
    expect(screen.queryByText('gotowe')).not.toBeInTheDocument()
  })

  it('does not present a completed project command as verified without evidence', () => {
    render(
      <ChatMessage
        message={{
          id: 901,
          role: 'assistant',
          status: 'completed',
          content: 'Odczyt zakończony.',
          createdAt: '2026-08-03T12:00:00Z',
          metadataJson: {
            kind: 'project_command_result',
            command_status: 'completed',
            evidence_count: 0,
            verified: false,
            verification_status: 'insufficient_evidence',
          },
        }}
      />,
    )

    expect(screen.getByText('ZAKOŃCZONE, BRAK WERYFIKACJI')).toBeInTheDocument()
    expect(screen.queryByText('ZWERYFIKOWANE')).not.toBeInTheDocument()
  })

  it('shows verified only when the backend verification flag is true', () => {
    render(
      <ChatMessage
        message={{
          id: 902,
          role: 'assistant',
          status: 'completed',
          content: 'Raport przeszedł drugi odczyt.',
          createdAt: '2026-08-03T12:00:00Z',
          metadataJson: {
            kind: 'project_command_result',
            command_status: 'completed',
            evidence_count: 2,
            verified: true,
            verification_status: 'verified',
          },
        }}
      />,
    )

    expect(screen.getByText('ZWERYFIKOWANE')).toBeInTheDocument()
    expect(screen.getByText('2 dowodów')).toBeInTheDocument()
  })

  it('renders provider and model metadata for assistant answers', () => {
    render(
      <ChatMessage
        message={{
          id: 77,
          role: 'assistant',
          status: 'completed',
          content: 'Gotowe.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            selected_provider_key: 'anthropic-cloud',
            selected_model_key: 'claude-sonnet-4-20250514',
            routing_reason: 'coding task routed to Claude',
            provider_posture: 'improve-when-needed',
            task_type: 'coding',
            llm_router_task_type: 'agent',
            llm_router_model: 'qwen3:14b',
            actual_runtime_model_name: 'claude-sonnet-4-20250514',
          },
        }}
      />,
    )

    expect(screen.getByText('AI ROUTING')).toBeInTheDocument()
    expect(screen.getByText('Użyto Claude')).toBeInTheDocument()
    expect(screen.getAllByText(/claude-sonnet-4-20250514/).length).toBeGreaterThan(0)
    expect(screen.getByText('Router')).toBeInTheDocument()
    expect(screen.getByText('agent')).toBeInTheDocument()
    expect(screen.getByText('Runtime')).toBeInTheDocument()
  })

  it('renders a code diff card and expands to the full diff', async () => {
    const user = userEvent.setup()

    render(
      <ChatMessage
        message={{
          id: 10,
          role: 'assistant',
          status: 'completed',
          content: 'Przygotowalem diff do review.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            kind: 'code_diff',
            title: 'Code change preview',
            summary: 'Zmiana obejmuje dwa pliki.',
            files: [
              {
                path: 'src/app.py',
                diff: '@@ -1 +1 @@\n-print("old")\n+print("new")',
              },
              {
                path: 'src/utils.py',
                diff: '@@ -1 +1 @@\n-return 1\n+return 2',
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText(/Code change preview/i)).toBeInTheDocument()
    expect(screen.getByText(/To jest tylko preview zmian/i)).toBeInTheDocument()
    expect(screen.queryByText(/return 2/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Zatwierdz preview/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Zastosuj zatwierdzone zmiany/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Pokaż pełny diff/i }))

    expect(screen.getByText(/return 2/i)).toBeInTheDocument()
  })

  it('splits code diff review into approve preview and apply approved changes', async () => {
    vi.mocked(api.approveActionDraft).mockResolvedValue({
      id: 81,
      accountId: 11,
      integrationKey: 'local-codegen',
      projectId: null,
      projectName: null,
      actionType: 'code_apply_preview',
      actionTitle: 'Apply code diff',
      actionSummary: 'Apply code diff',
      riskLevel: 'high',
      status: 'approved',
      requiresApproval: true,
      previewPayload: {},
      approvalNotes: null,
      rejectionReason: null,
      auditLog: [],
      createdBy: 'atnos',
      approvedAt: null,
      rejectedAt: null,
      executedAt: null,
      requiredCapabilities: ['connector_write'],
      accountSummary: {
        id: 11,
        label: 'Local Codegen',
        providerKey: 'local-codegen',
        accountIdentifier: 'local-codegen',
        status: 'configured',
        riskLevel: 'medium',
        maskedSecretState: 'Metadata only',
        allowedCapabilities: ['connector_write'],
      },
      createdAt: '2026-05-10T10:00:00Z',
      updatedAt: '2026-05-10T10:00:00Z',
    })
    vi.mocked(api.executeActionDraft).mockResolvedValue({
      actionDraft: {
        id: 81,
        accountId: 11,
        integrationKey: 'local-codegen',
        projectId: null,
        projectName: null,
        actionType: 'code_apply_preview',
        actionTitle: 'Apply code diff',
        actionSummary: 'Apply code diff',
        riskLevel: 'high',
        status: 'executed_later',
        requiresApproval: true,
        previewPayload: {},
        approvalNotes: null,
        rejectionReason: null,
        auditLog: [],
        createdBy: 'atnos',
        approvedAt: '2026-05-10T10:01:00Z',
        rejectedAt: null,
        executedAt: '2026-05-10T10:02:00Z',
        requiredCapabilities: ['connector_write'],
        accountSummary: {
          id: 11,
          label: 'Local Codegen',
          providerKey: 'local-codegen',
          accountIdentifier: 'local-codegen',
          status: 'configured',
          riskLevel: 'medium',
          maskedSecretState: 'Metadata only',
          allowedCapabilities: ['connector_write'],
        },
        createdAt: '2026-05-10T10:00:00Z',
        updatedAt: '2026-05-10T10:02:00Z',
      },
      executionResult: { status: 'applied' },
    })

    const user = userEvent.setup()
    render(
      <ChatMessage
        message={{
          id: 10,
          role: 'assistant',
          status: 'completed',
          content: 'Przygotowalem diff do review.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            kind: 'code_diff',
            title: 'Code change preview',
            summary: 'Zmiana obejmuje jeden plik.',
            action_draft_id: 81,
            draft_status: 'draft',
            files: [
              {
                path: 'src/app.py',
                diff: '@@ -1 +1 @@\n-print("old")\n+print("new")',
              },
            ],
          },
        }}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Zatwierdz preview/i }))

    await waitFor(() => {
      expect(api.approveActionDraft).toHaveBeenCalledWith(81)
      expect(api.executeActionDraft).not.toHaveBeenCalled()
    })

    expect(await screen.findByText('approved for apply')).toBeInTheDocument()
    expect(screen.getByText(/Preview jest zatwierdzony/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Zastosuj zatwierdzone zmiany/i }))

    await waitFor(() => {
      expect(api.executeActionDraft).toHaveBeenCalledWith(81, { confirm: true })
    })

    expect(await screen.findByText('applied')).toBeInTheDocument()
  })

  it('renders a runtime error card and wires retry plus fallback actions', async () => {
    const user = userEvent.setup()
    const onRetryRuntimeError = vi.fn()

    render(
      <MemoryRouter>
        <ChatMessage
          message={{
            id: 42,
            role: 'assistant',
            status: 'failed',
            content: '',
            createdAt: '2026-05-10T10:00:00Z',
            metadataJson: {
              runtime_error: {
                failure_type: 'request_timeout',
                provider_key: 'ollama-local',
                model_key: 'ollama-local/chat-default',
                runtime_model_name: 'atnos-core',
                user_title: 'Lokalny model nie odpowiedzial na czas',
                user_detail: 'Model lokalny przekroczył limit czasu i nie zakończył generacji.',
                retryable: true,
                can_fallback: true,
                suggested_actions: ['Spróbuj ponownie'],
                diagnostics: {
                  timeout_ms: 45000,
                  fallback_requires_confirmation: true,
                },
              },
            },
          }}
          onRetryRuntimeError={onRetryRuntimeError}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('Lokalny model nie odpowiedzial na czas')).toBeInTheDocument()
    expect(screen.getByText('request_timeout')).toBeInTheDocument()
    expect(screen.getByText('fallback:yes')).toBeInTheDocument()
    expect(screen.getByText('timeout:45000ms')).toBeInTheDocument()
    expect(screen.getByText('atnos-core')).toBeInTheDocument()
    expect(screen.getByText('Spróbuj ponownie')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Spróbuj ponownie' }))
    expect(onRetryRuntimeError).toHaveBeenCalledWith(42)

    await user.click(screen.getByRole('button', { name: 'Użyj fallbacku' }))
    expect(onRetryRuntimeError).toHaveBeenCalledWith(42, { forceExternal: true })
  })

  it('renders retry affordance for failed optimistic user messages', async () => {
    const user = userEvent.setup()
    const onRetryUserMessage = vi.fn()

    render(
      <ChatMessage
        message={{
          id: 'client-msg-1',
          role: 'user',
          status: 'failed',
          content: 'Hej Atnos',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            optimistic: true,
            optimisticState: 'failed',
            optimisticErrorMessage: 'ATNOS nie odpowiedział w ciągu 8 sekund.',
          },
        }}
        onRetryUserMessage={onRetryUserMessage}
      />,
    )

    expect(screen.getByText('ATNOS nie odpowiedział w ciągu 8 sekund.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Spróbuj ponownie' }))
    expect(onRetryUserMessage).toHaveBeenCalledWith('client-msg-1')
  })

  it('persists assistant question answers and refreshes the conversation', async () => {
    const user = userEvent.setup()
    const onQuestionAnswered = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({ selectedConversationId: 101 })
    vi.mocked(api.answerQuestion).mockResolvedValue({
      ok: true,
      assistantMessageId: 55,
      userMessageId: 56,
      nextAssistantMessageId: 57,
    })

    render(
      <ChatMessage
        message={{
          id: 55,
          role: 'assistant',
          status: 'completed',
          content: 'Wybierz kierunek shorta.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            kind: 'question_choice',
            question_id: 'tone-q1',
            prompt: 'Jaki typ shorta robimy?',
            options: [
              { id: 'a', label: 'edukacyjny' },
              { id: 'b', label: 'kontrowersyjny' },
              { id: 'c', label: 'storytelling' },
              { id: 'd', label: 'newsowy' },
            ],
            multi_select: false,
            required: true,
          },
        }}
        onQuestionAnswered={onQuestionAnswered}
      />,
    )

    await user.click(screen.getByRole('radio', { name: /kontrowersyjny/i }))

    await waitFor(() => {
      expect(api.answerQuestion).toHaveBeenCalledWith({
        conversationId: 101,
        assistantMessageId: 55,
        questionId: 'tone-q1',
        selectedIds: ['b'],
      })
    })

    expect(onQuestionAnswered).toHaveBeenCalledWith(101)
    expect(useChatStore.getState().messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: 'user',
          content: 'Wybralem: kontrowersyjny',
        }),
      ]),
    )
  })

  it('shows answer-question errors and retries without adding another optimistic message', async () => {
    const user = userEvent.setup()
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    useChatStore.setState({ selectedConversationId: 101 })
    vi.mocked(api.answerQuestion)
      .mockRejectedValueOnce(new Error('Backend odrzucil wybor.'))
      .mockResolvedValueOnce({
        ok: true,
        assistantMessageId: 55,
        userMessageId: 56,
        nextAssistantMessageId: 57,
      })

    render(
      <ChatMessage
        message={{
          id: 55,
          role: 'assistant',
          status: 'completed',
          content: 'Wybierz kierunek shorta.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            kind: 'question_choice',
            question_id: 'tone-q1',
            prompt: 'Jaki typ shorta robimy?',
            options: [
              { id: 'a', label: 'edukacyjny' },
              { id: 'b', label: 'kontrowersyjny' },
            ],
            multi_select: false,
            required: true,
          },
        }}
        onQuestionAnswered={vi.fn().mockResolvedValue(undefined)}
      />,
    )

    await user.click(screen.getByRole('radio', { name: /kontrowersyjny/i }))

    expect(await screen.findByText('Backend odrzucil wybor.')).toBeInTheDocument()
    expect(useChatStore.getState().messages.filter((message) => message.role === 'user')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Ponow' }))

    await waitFor(() => {
      expect(api.answerQuestion).toHaveBeenCalledTimes(2)
    })
    expect(useChatStore.getState().messages.filter((message) => message.role === 'user')).toHaveLength(1)

    consoleErrorSpy.mockRestore()
  })

  it('renders a creative video workflow card with inline video attachment', () => {
    const { container } = render(
      <ChatMessage
        message={{
          id: 501,
          role: 'assistant',
          status: 'completed',
          content: 'Short jest gotowy do review.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            creative_video_operator: {
              workflow_key: 'content_campaign',
              platform_hint: 'youtube',
              quality_profile: 'premium',
              approval_required: true,
              artifact_count: 3,
              quality_run: {
                status: 'DONE_VERIFIED',
                score: 8.7,
                report_path: 'F:/Apps/Atnos/data/quality-runs/20260519_content_campaign.md',
                evidence_count: 3,
              },
              next_step: 'Sprawdz material i zdecyduj, czy publikowac.',
              workflow_state: {
                current_node: 'graph.approval_gate',
                status: 'awaiting_human',
              },
            },
          },
          attachments: [
            {
              attachmentId: 1,
              filename: 'short.mp4',
              storagePath: 'atnos-outputs/short.mp4',
              kind: 'video',
              source: 'assistant',
              status: 'available',
              mediaType: 'video/mp4',
              fileExtension: '.mp4',
              sizeBytes: 2_048_000,
            },
            {
              attachmentId: 2,
              filename: 'thumbnail.png',
              storagePath: 'atnos-outputs/thumbnail.png',
              kind: 'image',
              source: 'assistant',
              status: 'available',
              mediaType: 'image/png',
              fileExtension: '.png',
              sizeBytes: 128_000,
            },
            {
              attachmentId: 3,
              filename: 'captions.srt',
              storagePath: 'atnos-outputs/captions.srt',
              kind: 'text',
              source: 'assistant',
              status: 'available',
              mediaType: 'text/plain',
              fileExtension: '.srt',
              sizeBytes: 2_048,
            },
          ],
        }}
      />,
    )

    expect(screen.getByText('VIDEO WORKFLOW')).toBeInTheDocument()
    expect(screen.getAllByText('Gotowe do review')).toHaveLength(2)
    expect(screen.getByText('Short pipeline')).toBeInTheDocument()
    expect(screen.getByText('Premium')).toBeInTheDocument()
    expect(screen.getByText('youtube')).toBeInTheDocument()
    expect(screen.getByText('short.mp4')).toBeInTheDocument()
    expect(screen.getByTestId('chat-quality-run-card')).toBeInTheDocument()
    expect(screen.getByText('Zweryfikowane')).toBeInTheDocument()
    expect(screen.getByText('score 8.7/10')).toBeInTheDocument()
    expect(screen.getByText('dowody 3')).toBeInTheDocument()
    expect(screen.getByText(/20260519_content_campaign\.md/i)).toBeInTheDocument()
    expect(screen.getByText('Sprawdz material i zdecyduj, czy publikowac.')).toBeInTheDocument()
    expect(container.querySelector('video')).not.toBeNull()
  })

  it('renders active status for queued creative video workflows', () => {
    render(
      <ChatMessage
        message={{
          id: 777,
          role: 'assistant',
          status: 'completed',
          content: 'Uruchomilem workflow shorta.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            creative_video_operator: {
              workflow_key: 'content_campaign',
              platform_hint: 'youtube',
              quality_profile: 'premium',
              approval_required: true,
              artifact_count: 0,
              next_step: 'Poczekaj na zakonczenie generacji, potem zrob review shorta i zdecyduj o publikacji.',
              workflow_state: {
                current_node: 'graph.research',
                status: 'queued',
              },
            },
          },
          attachments: [],
        }}
      />,
    )

    expect(screen.getAllByText('Zaplanowane')).toHaveLength(2)
    expect(
      screen.getByText(
        'Workflow jest aktywny. Aktualny etap i artefakty odswiezaja sie z metadanych zadania.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/ATNOS realnie pracuje teraz: Uklada brief i research/i)).toBeInTheDocument()
    expect(screen.getByText('Poczekaj na zakonczenie generacji, potem zrob review shorta i zdecyduj o publikacji.')).toBeInTheDocument()
  })

  it('renders blocked status for failed creative video workflows', () => {
    render(
      <ChatMessage
        message={{
          id: 778,
          role: 'assistant',
          status: 'completed',
          content: 'Workflow shorta zatrzymal sie w tle.',
          createdAt: '2026-05-10T10:00:00Z',
          metadataJson: {
            creative_video_operator: {
              workflow_key: 'content_campaign',
              platform_hint: 'youtube',
              quality_profile: 'premium',
              blocked: true,
              blocker: 'Free VRAM below 6 GB floor.',
              approval_required: false,
              artifact_count: 0,
              next_step: 'Zwolnij GPU albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie.',
              workflow_state: {
                current_node: 'hardware_guard',
                status: 'failed',
              },
            },
          },
          attachments: [],
        }}
      />,
    )

    expect(screen.getAllByText('Zatrzymane')).toHaveLength(2)
    expect(screen.getByText('Free VRAM below 6 GB floor.')).toBeInTheDocument()
    expect(
      screen.getByText('Zwolnij GPU albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Sprawdza zasoby PC')).toBeInTheDocument()
  })

  it('shows agentic tool timeline with status and summary', () => {
    render(
      <ChatMessage
        message={{
          id: 950,
          role: 'assistant',
          status: 'completed',
          content: 'Otworzyłem Comfy Desktop.',
          createdAt: '2026-09-15T20:00:00Z',
          metadataJson: {
            agentic_tool_loop: {
              status: 'completed',
              turns_used: 2,
              tools_used: ['desktop_app_open'],
              tool_calls: [
                {
                  name: 'desktop_app_open',
                  status: 'opened',
                  summary: 'Opened Comfy Desktop (comfy-desktop).',
                  arguments: { query: 'Comfort' },
                },
              ],
              settings: {
                files_enabled: true,
                memory_enabled: true,
                internet_enabled: false,
                work_mode: 'quick',
              },
            },
          },
        }}
      />,
    )

    expect(screen.getByTestId('agentic-tool-activity')).toBeInTheDocument()
    expect(screen.getByTestId('agentic-tool-timeline')).toBeInTheDocument()
    expect(screen.getByText('gotowe')).toBeInTheDocument()
    expect(screen.getByText('Otwarto aplikację')).toBeInTheDocument()
    expect(screen.getByText('OK')).toBeInTheDocument()
    expect(screen.getByText('Opened Comfy Desktop (comfy-desktop).')).toBeInTheDocument()
    expect(screen.queryByText('desktop_app_open')).not.toBeInTheDocument()
    expect(screen.queryByText(/ustawienia:/i)).not.toBeInTheDocument()
  })
})
