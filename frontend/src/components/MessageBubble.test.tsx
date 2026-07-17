import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../store/types'

function buildAssistantMessage(overrides?: Partial<Extract<Message, { role: 'assistant' }>>): Extract<Message, { role: 'assistant' }> {
  return {
    id: 'a1',
    role: 'assistant',
    content: 'Answer',
    ts: Date.now(),
    answer_mode: 'confident',
    grounded: true,
    refusal: false,
    evidence: [],
    used_evidence_ids: [],
    follow_ups: [],
    metrics: {},
    ...overrides,
  }
}

describe('MessageBubble', () => {
  it('renders retry action for error message', async () => {
    const onRetry = vi.fn()

    render(
      <MessageBubble
        message={{
          id: 'e1',
          role: 'error',
          content: 'failed',
          ts: Date.now(),
          retry_query: 'what is grace',
          retry_corpus: 'clough',
          retry_model: 'm1',
        }}
        onRetry={onRetry}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledWith('what is grace', 'clough', 'm1')
  })

  it('hides follow ups section when suggestions are empty', () => {
    render(<MessageBubble message={buildAssistantMessage({ follow_ups: [] })} />)
    expect(screen.queryByText(/follow ups/i)).toBeNull()
  })

  it('does not render follow ups section for refusal answers', () => {
    render(
      <MessageBubble
        message={buildAssistantMessage({
          refusal: true,
          answer_mode: 'refuse',
          follow_ups: ['Can you simplify that?'],
        })}
      />
    )

    expect(screen.queryByText(/follow ups/i)).toBeNull()
    expect(screen.queryByText(/can you simplify that\?/i)).toBeNull()
  })

  it('announces streaming status with aria-live', () => {
    render(
      <MessageBubble
        message={buildAssistantMessage({
          isStreaming: true,
          phase: 'retrieving',
          status_message: 'Finding evidence',
          committed_blocks: [],
          stream_buffer: '',
          stream_in_code: false,
        })}
      />
    )

    const statusRegion = screen
      .getAllByRole('status')
      .find((node) => node.getAttribute('aria-live') === 'polite' && node.textContent?.includes('Finding evidence'))
    expect(statusRegion).not.toBeNull()
  })

  it('keeps simple streaming status without rendering stepper', () => {
    render(
      <MessageBubble
        message={buildAssistantMessage({
          isStreaming: true,
          phase: 'building',
          status_message: 'Organizing context',
          committed_blocks: [],
          stream_buffer: '',
          stream_in_code: false,
        })}
      />
    )

    expect(screen.queryByRole('list', { name: /streaming phases/i })).toBeNull()
    expect(screen.getByText('Organizing context')).not.toBeNull()
  })

  it('passes grouped citation context when opening inspector from sources', async () => {
    const onInspectEvidence = vi.fn()
    const evidence = [
      {
        evidence_id: 'ev-1',
        parent_id: 'p1',
        label: 'Source 1',
        path: 'path-1',
        is_primary: true,
        context_only: false,
        preview: 'Preview text one',
      },
      {
        evidence_id: 'ev-2',
        parent_id: 'p2',
        label: 'Source 2',
        path: 'path-2',
        is_primary: true,
        context_only: false,
        preview: 'Preview text two',
      },
    ]

    render(
      <MessageBubble
        message={buildAssistantMessage({
          evidence,
        })}
        onInspectEvidence={onInspectEvidence}
      />
    )

    const sourceCard = screen.getByText('Source 1').closest('button') as HTMLButtonElement
    await userEvent.click(sourceCard)

    expect(onInspectEvidence).toHaveBeenCalledWith(evidence, 0)
  })

  it('renders SOURCE/ordinal citation tokens as citation chips', () => {
    render(
      <MessageBubble
        message={buildAssistantMessage({
          content: 'Law exposes sin [SOURCE 1]. Grace follows [chunk:2].',
          evidence: [
            {
              evidence_id: 'ev-1',
              parent_id: 'p1',
              label: 'Source 1',
              path: 'path-1',
              is_primary: true,
              context_only: false,
              preview: 'Preview one',
            },
            {
              evidence_id: 'ev-2',
              parent_id: 'p2',
              label: 'Source 2',
              path: 'path-2',
              is_primary: true,
              context_only: false,
              preview: 'Preview two',
            },
          ],
        })}
      />
    )

    expect(screen.queryByText(/\[SOURCE 1\]/i)).toBeNull()
    expect(screen.getByRole('button', { name: /citation 1/i })).not.toBeNull()
    expect(screen.getByRole('button', { name: /citation 2/i })).not.toBeNull()
  })

  it('renders a readable fallback evidence reason', () => {
    render(
      <MessageBubble
        message={buildAssistantMessage({
          citation_fallback_reason: 'narrow_answer_no_primary_matches_found',
        })}
      />
    )

    expect(screen.getByText(/No direct citation anchors were found/i)).not.toBeNull()
    expect(screen.queryByText('narrow_answer_no_primary_matches_found')).toBeNull()
  })
})
