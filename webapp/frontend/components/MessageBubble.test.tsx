import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from './MessageBubble';

vi.mock('framer-motion', () => ({
  motion: { div: ({ children, ...p }: React.ComponentProps<'div'>) => <div {...p}>{children}</div> },
  useReducedMotion: () => true,
}));

describe('MessageBubble', () => {
  it('renders an assistant message as markdown (real <strong>)', () => {
    const { container } = render(
      <MessageBubble message={{ role: 'assistant', text: 'a **bold** word', ts: '1' }} />,
    );
    expect(container.querySelector('strong')?.textContent).toBe('bold');
  });

  it('renders a user message as plain text (no markdown parsing)', () => {
    const { container } = render(
      <MessageBubble message={{ role: 'user', text: 'a **bold** word', ts: '2' }} />,
    );
    expect(container.querySelector('strong')).toBeNull();
    expect(container.textContent).toContain('a **bold** word');
  });
});
