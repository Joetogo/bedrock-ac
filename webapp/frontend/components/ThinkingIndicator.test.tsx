import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

// Render framer-motion elements as their plain DOM tag, dropping animation-only
// props so React doesn't warn and so text assertions are stable. The helper
// lives inside the factory because vi.mock is hoisted above module-level consts.
vi.mock('framer-motion', () => {
  const passthrough =
    (Tag: string) =>
    ({ children, animate, initial, transition, whileHover, ...rest }: Record<string, unknown>) =>
      <Tag {...rest}>{children as JSX.Element}</Tag>;
  return {
    motion: {
      div: passthrough('div'),
      span: passthrough('span'),
      svg: passthrough('svg'),
      g: passthrough('g'),
    },
    useReducedMotion: () => false,
  };
});

describe('ThinkingIndicator', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows the starting word (and its language) and a status role', () => {
    const { container, getByRole } = render(<ThinkingIndicator startIndex={0} />);
    expect(getByRole('status')).toBeTruthy();
    expect(container.textContent).toContain('thinking');
    expect(container.textContent).toContain('English');
    // the starburst animation is present
    expect(container.querySelector('svg')).toBeTruthy();
  });

  it('cycles to the next word after the interval elapses', () => {
    const { container } = render(<ThinkingIndicator startIndex={0} />);
    expect(container.textContent).toContain('thinking');
    act(() => {
      vi.advanceTimersByTime(1700);
    });
    expect(container.textContent).toContain('whakaaro');
    expect(container.textContent).not.toContain('thinking');
  });

  it('wraps around to the first word after the last', () => {
    // startIndex 13 is the last entry; one tick should wrap back to index 0.
    const { container } = render(<ThinkingIndicator startIndex={13} />);
    act(() => {
      vi.advanceTimersByTime(1700);
    });
    expect(container.textContent).toContain('thinking');
  });
});
