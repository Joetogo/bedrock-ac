import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { ChartBlock } from './ChartBlock';

// Mock vega-embed: default export returns a fake view with the image APIs.
const view = {
  toCanvas: vi.fn(async () => {
    const c = document.createElement('canvas');
    (c as unknown as { toBlob: (cb: (b: Blob) => void) => void }).toBlob = (cb) =>
      cb(new Blob(['x'], { type: 'image/png' }));
    return c;
  }),
  toImageURL: vi.fn(async () => 'data:image/png;base64,AAAA'),
  finalize: vi.fn(),
};
const embed = vi.fn(async () => ({ view }));
vi.mock('vega-embed', () => ({ default: (...a: unknown[]) => embed(...a) }));
vi.mock('vega', () => ({ loader: () => ({ http: () => {}, load: () => {} }) }));

// A valid Vega-Lite spec that the visx renderer does NOT handle (mark: tick),
// so ChartBlock takes the vega-embed fallback path these tests exercise. Simple
// bar/line/area/point specs now render via visx (covered in lib/chartSpec.test.ts).
const VALID = JSON.stringify({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  title: 'Temp', data: { values: [{ t: '2026-07-09T01:00Z', temp: 21 }] },
  mark: 'tick', encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'temp', type: 'quantitative' } },
});

beforeEach(() => {
  embed.mockClear(); view.toCanvas.mockClear(); view.toImageURL.mockClear();
  // jsdom has no matchMedia; default to light.
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }) as unknown as typeof window.matchMedia;
});

describe('ChartBlock', () => {
  it('embeds a valid spec (parsed object) and shows a toolbar', async () => {
    const { getByRole } = render(<ChartBlock spec={VALID} />);
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1));
    const passedSpec = embed.mock.calls[0][1] as { title: string };
    expect(passedSpec.title).toBe('Temp');                 // parsed to an object, not a string
    expect(getByRole('button', { name: /copy image/i })).toBeTruthy();
  });

  it('falls back (no throw, no embed) on invalid JSON', async () => {
    const { container, queryByRole } = render(<ChartBlock spec={'{ not json'} />);
    await waitFor(() => expect(container.querySelector('pre')).toBeTruthy());
    expect(embed).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(/couldn.t render/i);
    expect(queryByRole('button', { name: /copy image/i })).toBeNull();
  });

  it('copy button renders a PNG via the view canvas API', async () => {
    const write = vi.fn(async () => {});
    // @ts-expect-error minimal clipboard mock
    navigator.clipboard = { write };
    // @ts-expect-error jsdom lacks ClipboardItem
    globalThis.ClipboardItem = class { constructor(public items: unknown) {} };
    const { getByRole } = render(<ChartBlock spec={VALID} />);
    await waitFor(() => expect(embed).toHaveBeenCalled());
    getByRole('button', { name: /copy image/i }).click();
    await waitFor(() => expect(view.toCanvas).toHaveBeenCalled());
  });

  it('download button asks the view for an image URL', async () => {
    const { getByRole } = render(<ChartBlock spec={VALID} />);
    await waitFor(() => expect(embed).toHaveBeenCalled());
    getByRole('button', { name: /download png/i }).click();
    await waitFor(() => expect(view.toImageURL).toHaveBeenCalledWith('png', expect.anything()));
  });
});
