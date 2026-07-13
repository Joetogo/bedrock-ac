# Web Console Rich Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent emit Vega-Lite v5 chart specs that the web console renders as interactive, exportable charts alongside proper markdown, replacing today's plain-text rendering.

**Architecture:** The agent's answer stays a markdown string on the existing async path (`POST /chat` → worker → DynamoDB → poll → frontend). That string may now contain fenced ` ```vega-lite ` blocks holding an inline-data JSON spec. A new `MessageContent` renderer parses assistant markdown with `react-markdown` + `remark-gfm` and routes chart fences to a new lazy-loaded `ChartBlock` (vega-embed). Nothing in the backend, runtime contract, or data path changes.

**Tech Stack:** Next.js 14 static export, React 18, TypeScript, Tailwind (media dark-mode), vitest + jsdom + @testing-library/react; new deps `react-markdown@9`, `remark-gfm@4`, `vega@5`, `vega-lite@5`, `vega-embed@6`. Agent side: Python 3.12, Strands, tested with pytest via `.venv-agent`.

## Global Constraints

- **Read-only upstream preserved.** No new tools, Lambdas, upstream calls, backend, runtime, or IaC changes. Frontend + agent-prompt only. The agent charts data it already fetched read-only.
- **Self-contained static export.** All new deps bundle into the Next.js static export; no CDN or external host at runtime.
- **No raw HTML.** `react-markdown` renders with its default safe component set; do NOT add `rehype-raw`.
- **Inline data only.** Vega specs must not load external URLs; `ChartBlock` disables Vega's http/file loading so only `data.values` is used.
- **Theme-aware.** Charts and markdown render correctly in both light and dark. Dark mode uses Tailwind's `media` strategy (`prefers-color-scheme`), NOT a `.dark` class — `ChartBlock` reads `window.matchMedia('(prefers-color-scheme: dark)')`.
- **Never throw out of a message.** A bad/oversized/unparseable spec falls back to a code block plus an inline "chart couldn't render" note; the rest of the message still renders.
- **Chart always accompanied.** The agent prompt (Task 4) must require a short text summary and a data table beside every chart. Starter vocabulary: line / bar / scatter. Specs use `"width": "container"`, include a `title` and axis labels, and cap ~200 data points.
- **Frontend location & commands.** All frontend work under `webapp/frontend/`. Install with `npm install`; run tests with `npm test` (vitest). Tests are co-located next to source (e.g. `components/Foo.test.tsx`).
- **Backend tests unchanged & green.** Python suites run with `.venv-agent/Scripts/python.exe -m pytest` from the repo root; no backend edits in this plan except the agent system prompt string (Task 4).

---

### Task 1: ChartBlock component

Renders one Vega-Lite spec as an interactive, exportable chart. Leaf component — nothing else depends on Tasks 2-4 yet. This task also installs the chart + component-testing dependencies that Tasks 2-3 reuse.

**Files:**
- Create: `webapp/frontend/components/ChartBlock.tsx`
- Create: `webapp/frontend/components/ChartBlock.test.tsx`
- Modify: `webapp/frontend/package.json` (deps via npm install)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `export function ChartBlock({ spec }: { spec: string }): JSX.Element` — `spec` is the raw string contents of a ` ```vega-lite ` fenced block. Task 2 imports and renders `<ChartBlock spec={raw} />`.

- [ ] **Step 1: Install dependencies**

Run from `webapp/frontend/`:
```bash
npm install vega@^5 vega-lite@^5 vega-embed@^6
npm install -D @testing-library/react@^16 @testing-library/dom@^10
```
Expected: `package.json` gains the three runtime deps and two dev deps; `npm install` exits 0.

- [ ] **Step 2: Write the failing test**

Create `webapp/frontend/components/ChartBlock.test.tsx`:
```tsx
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

const VALID = JSON.stringify({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  title: 'Temp', data: { values: [{ t: '2026-07-09T01:00Z', temp: 21 }] },
  mark: 'line', encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'temp', type: 'quantitative' } },
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- ChartBlock`
Expected: FAIL — `ChartBlock` not found / module has no export `ChartBlock`.

- [ ] **Step 4: Write the implementation**

Create `webapp/frontend/components/ChartBlock.tsx`:
```tsx
'use client';
import { useEffect, useRef, useState } from 'react';

type View = {
  toCanvas: () => Promise<HTMLCanvasElement>;
  toImageURL: (type: 'png' | 'svg', scale?: number) => Promise<string>;
  finalize: () => void;
};

function useDarkMode(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    setDark(mq.matches);
    const on = () => setDark(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return dark;
}

function triggerDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function ChartBlock({ spec }: { spec: string }) {
  const dark = useDarkMode();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<View | null>(null);
  const [failed, setFailed] = useState(false);

  // Parse once; invalid JSON -> fallback, never throw.
  let parsed: Record<string, unknown> | null = null;
  try {
    const obj = JSON.parse(spec);
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) parsed = obj;
  } catch {
    parsed = null;
  }

  useEffect(() => {
    if (!parsed || !hostRef.current) {
      if (!parsed) setFailed(true);
      return;
    }
    let cancelled = false;
    setFailed(false);
    (async () => {
      try {
        const [{ default: embed }, vega] = await Promise.all([
          import('vega-embed'),
          import('vega'),
        ]);
        // Inline data only: reject any URL/file load attempt.
        const loader = vega.loader();
        loader.http = () => Promise.reject(new Error('external data disabled'));
        loader.load = () => Promise.reject(new Error('external data disabled'));
        const result = await embed(hostRef.current as HTMLElement, parsed as object, {
          actions: { export: true, source: false, compiled: false, editor: false },
          theme: dark ? 'dark' : undefined,
          renderer: 'canvas',
          loader,
        });
        if (cancelled) {
          result.view.finalize();
          return;
        }
        viewRef.current = result.view as unknown as View;
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Re-embed when the parsed spec text or theme changes.
  }, [spec, dark]); // eslint-disable-line react-hooks/exhaustive-deps

  async function copyImage() {
    const view = viewRef.current;
    if (!view) return;
    try {
      const canvas = await view.toCanvas();
      await new Promise<void>((resolve) =>
        canvas.toBlob(async (blob) => {
          if (blob && navigator.clipboard && 'write' in navigator.clipboard) {
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
          }
          resolve();
        }, 'image/png'),
      );
    } catch {
      /* clipboard unavailable — no-op */
    }
  }

  async function download(type: 'png' | 'svg') {
    const view = viewRef.current;
    if (!view) return;
    try {
      const url = await view.toImageURL(type, type === 'png' ? 2 : 1);
      triggerDownload(url, `chart.${type}`);
    } catch {
      /* ignore */
    }
  }

  if (failed || !parsed) {
    return (
      <div className="my-2">
        <pre className="overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">
          <code>{spec}</code>
        </pre>
        <p className="mt-1 text-xs text-slate-500">chart couldn&apos;t render — showing the raw spec</p>
      </div>
    );
  }

  return (
    <figure className="my-3">
      <div className="flex justify-end gap-2 pb-1">
        <button
          type="button"
          onClick={copyImage}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Copy image
        </button>
        <button
          type="button"
          onClick={() => download('png')}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Download PNG
        </button>
        <button
          type="button"
          onClick={() => download('svg')}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Download SVG
        </button>
      </div>
      <div ref={hostRef} className="w-full" />
    </figure>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test -- ChartBlock`
Expected: PASS (4 tests). If the `ClipboardItem`/`toBlob` mock timing flakes, note the copy assertion only checks `view.toCanvas` was called — keep it.

- [ ] **Step 6: Commit**

```bash
git add webapp/frontend/components/ChartBlock.tsx webapp/frontend/components/ChartBlock.test.tsx webapp/frontend/package.json webapp/frontend/package-lock.json
git commit -m "feat(console): ChartBlock renders Vega-Lite specs with export"
```

---

### Task 2: MessageContent markdown renderer

Parses an assistant message as markdown (GFM tables/headings/bold) and routes ` ```vega-lite ` / ` ```vega ` fences to `ChartBlock`. Installs `react-markdown` + `remark-gfm`.

**Files:**
- Create: `webapp/frontend/components/MessageContent.tsx`
- Create: `webapp/frontend/components/MessageContent.test.tsx`
- Modify: `webapp/frontend/package.json` (deps via npm install)

**Interfaces:**
- Consumes: `ChartBlock` from Task 1 — `<ChartBlock spec={string} />`.
- Produces: `export function MessageContent({ text }: { text: string }): JSX.Element`. Task 3's `MessageBubble` renders `<MessageContent text={message.text} />` for assistant messages.

- [ ] **Step 1: Install dependencies**

Run from `webapp/frontend/`:
```bash
npm install react-markdown@^9 remark-gfm@^4
```
Expected: both appear in `package.json` dependencies; exit 0.

- [ ] **Step 2: Write the failing test**

Create `webapp/frontend/components/MessageContent.test.tsx`:
```tsx
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MessageContent } from './MessageContent';

// Mock ChartBlock so we assert routing, not chart rendering.
vi.mock('./ChartBlock', () => ({
  ChartBlock: ({ spec }: { spec: string }) => <div data-testid="chart" data-spec={spec} />,
}));

describe('MessageContent', () => {
  it('renders a GFM table as a real <table>', () => {
    const md = ['| room | co2 |', '| --- | --- |', '| Opotiki | 470 |'].join('\n');
    const { container } = render(<MessageContent text={md} />);
    expect(container.querySelector('table')).toBeTruthy();
    expect(container.querySelector('td')?.textContent).toBe('Opotiki');
  });

  it('routes a vega-lite fence to ChartBlock with the raw spec', () => {
    const spec = '{"mark":"bar"}';
    const md = ['Here is a chart:', '', '```vega-lite', spec, '```'].join('\n');
    const { getByTestId } = render(<MessageContent text={md} />);
    expect(getByTestId('chart').getAttribute('data-spec')).toBe(spec);
  });

  it('leaves a normal code fence as a code block, not a chart', () => {
    const md = ['```json', '{"a":1}', '```'].join('\n');
    const { container, queryByTestId } = render(<MessageContent text={md} />);
    expect(queryByTestId('chart')).toBeNull();
    expect(container.querySelector('code')).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- MessageContent`
Expected: FAIL — `MessageContent` not found.

- [ ] **Step 4: Write the implementation**

Create `webapp/frontend/components/MessageContent.tsx`. Note: react-markdown v9's `code` component does NOT receive an `inline` prop — a fenced block is detected by the `language-*` className; inline code has no className.
```tsx
'use client';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartBlock } from './ChartBlock';

const components: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || '');
    const lang = match?.[1];
    const raw = String(children).replace(/\n$/, '');
    if (lang === 'vega-lite' || lang === 'vega') {
      return <ChartBlock spec={raw} />;
    }
    if (match) {
      return (
        <pre className="overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">
          <code className={className} {...props}>{children}</code>
        </pre>
      );
    }
    return (
      <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.85em] dark:bg-slate-800" {...props}>
        {children}
      </code>
    );
  },
  table({ children }) {
    return (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return <th className="border-b border-slate-300 px-2 py-1 font-semibold dark:border-slate-600">{children}</th>;
  },
  td({ children }) {
    return <td className="border-b border-slate-200 px-2 py-1 dark:border-slate-700">{children}</td>;
  },
  a({ children, href }) {
    return <a href={href} className="text-accent underline" target="_blank" rel="noreferrer">{children}</a>;
  },
  ul({ children }) {
    return <ul className="my-1 list-disc pl-5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="my-1 list-decimal pl-5">{children}</ol>;
  },
  h1({ children }) {
    return <h1 className="mb-1 mt-2 text-base font-semibold">{children}</h1>;
  },
  h2({ children }) {
    return <h2 className="mb-1 mt-2 text-sm font-semibold">{children}</h2>;
  },
  h3({ children }) {
    return <h3 className="mb-1 mt-2 text-sm font-semibold">{children}</h3>;
  },
  p({ children }) {
    return <p className="my-1 leading-relaxed">{children}</p>;
  },
};

export function MessageContent({ text }: { text: string }) {
  // No rehype-raw: raw HTML stays inert. remark-gfm enables tables/strikethrough/task lists.
  return (
    <div className="max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test -- MessageContent`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add webapp/frontend/components/MessageContent.tsx webapp/frontend/components/MessageContent.test.tsx webapp/frontend/package.json webapp/frontend/package-lock.json
git commit -m "feat(console): MessageContent renders markdown and routes chart fences"
```

---

### Task 3: Wire MessageBubble to MessageContent

Assistant messages render through `MessageContent`; user messages keep the current plain bubble. Entrance animation and reduced-motion guard are preserved.

**Files:**
- Modify: `webapp/frontend/components/MessageBubble.tsx`
- Create: `webapp/frontend/components/MessageBubble.test.tsx`

**Interfaces:**
- Consumes: `MessageContent` from Task 2 — `<MessageContent text={string} />`.
- Produces: unchanged public surface — `export function MessageBubble({ message }: { message: Message }): JSX.Element`. `ChatThread.tsx` already renders it and is not modified.

- [ ] **Step 1: Write the failing test**

Create `webapp/frontend/components/MessageBubble.test.tsx`:
```tsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- MessageBubble`
Expected: FAIL — assistant message currently renders literal `**bold**`, so `strong` is null.

- [ ] **Step 3: Write the implementation**

Replace `webapp/frontend/components/MessageBubble.tsx`:
```tsx
'use client';
import { motion, useReducedMotion } from 'framer-motion';
import type { Message } from '@/lib/types';
import { MessageContent } from './MessageContent';

export function MessageBubble({ message }: { message: Message }) {
  const reduce = useReducedMotion();
  const isUser = message.role === 'user';
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div className={`max-w-[80ch] rounded-2xl px-4 py-2 text-sm ${
        isUser
          ? 'whitespace-pre-wrap bg-accent text-white'
          : 'bg-slate-100 text-ink dark:bg-slate-800 dark:text-slate-100'
      }`}>
        {isUser ? message.text : <MessageContent text={message.text} />}
      </div>
    </motion.div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- MessageBubble`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full frontend suite**

Run: `npm test`
Expected: all suites green — the pre-existing api/auth/pkce tests (10) plus ChartBlock (4), MessageContent (3), MessageBubble (2).

- [ ] **Step 6: Commit**

```bash
git add webapp/frontend/components/MessageBubble.tsx webapp/frontend/components/MessageBubble.test.tsx
git commit -m "feat(console): render assistant messages via MessageContent"
```

---

### Task 4: Agent Visualization prompt

Teach the agent to emit Vega-Lite specs when a chart helps, always beside a summary and a data table, inline data only.

**Files:**
- Modify: `agent/agent.py` (the `SYSTEM_PROMPT` constant, currently lines 21-26)
- Modify: `tests/test_agent_helpers.py` (add one prompt-content test)

**Interfaces:**
- Consumes: nothing.
- Produces: an extended `SYSTEM_PROMPT` string. No signature change; `answer()` already passes `SYSTEM_PROMPT` to `run_agent`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_helpers.py`:
```python
def test_system_prompt_has_visualization_guidance():
    p = A.SYSTEM_PROMPT.lower()
    assert "vega-lite" in p                        # names the format
    assert "```vega-lite" in A.SYSTEM_PROMPT        # tells it to use a fenced block
    assert "inline" in p                            # inline data only
    assert "table" in p                             # a data table must accompany the chart
    assert "container" in p                         # width: container guidance
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-agent/Scripts/python.exe -m pytest tests/test_agent_helpers.py::test_system_prompt_has_visualization_guidance -v`
Expected: FAIL — current prompt has no visualization text.

- [ ] **Step 3: Write the implementation**

Replace the `SYSTEM_PROMPT` assignment in `agent/agent.py` (lines 21-26):
```python
SYSTEM_PROMPT = (
    "You answer questions about meeting-room conditions and Teams call "
    "quality by calling the provided tools. Call records appear ~30 min "
    "after a call ends. When correlating, state correlation is "
    "observational, not causal. Be concise.\n\n"
    "Visualization: when a trend, comparison, or correlation is clearer as "
    "a chart (or the user asks for one), emit a valid Vega-Lite v5 spec "
    "inside a fenced ```vega-lite code block. Put the data inline under "
    "data.values (never reference an external URL); keep it to ~200 points "
    'and set "width":"container" with a title and axis labels. Do not set a '
    "fixed pixel width or height. ALWAYS accompany a chart with a one- or "
    "two-sentence summary and a markdown data table of the same numbers, so "
    "the answer is useful even if the chart fails. Prefer line for a metric "
    "over time, bar for a comparison across categories, and scatter (point) "
    "for a correlation. Example:\n"
    "```vega-lite\n"
    '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json",'
    '"title":"Temp over time","width":"container",'
    '"data":{"values":[{"t":"2026-07-09T01:00Z","temp":21.1},'
    '{"t":"2026-07-09T02:00Z","temp":21.6}]},"mark":"line",'
    '"encoding":{"x":{"field":"t","type":"temporal","title":"Time"},'
    '"y":{"field":"temp","type":"quantitative","title":"C"}}}\n'
    "```"
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-agent/Scripts/python.exe -m pytest tests/test_agent_helpers.py::test_system_prompt_has_visualization_guidance -v`
Expected: PASS.

- [ ] **Step 5: Run the agent + handler suites to confirm no regression**

Run: `.venv-agent/Scripts/python.exe -m pytest tests/test_agent_helpers.py tests/test_handlers.py -v`
Expected: all PASS (existing tests + the new one).

- [ ] **Step 6: Commit**

```bash
git add agent/agent.py tests/test_agent_helpers.py
git commit -m "feat(agent): system prompt emits Vega-Lite charts with summary and table"
```

---

### Task 5: Static-export build verification

Prove the new deps bundle into the static export with no CDN/runtime fetch and no type errors — the release gate before any deploy. No deploy happens in this task (see Deployment section).

**Files:**
- None modified. Verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a clean `webapp/frontend/out/` static export.

- [ ] **Step 1: Full frontend test suite**

Run from `webapp/frontend/`: `npm test`
Expected: all suites green (19 tests: 10 pre-existing + 9 new).

- [ ] **Step 2: Production static export**

Run from `webapp/frontend/`: `npm run build`
Expected: `next build` completes with "Exporting (static)"; `out/` regenerated; no type errors. vega-embed is dynamically imported, so it lands in a lazy chunk, not the main bundle. If build fails with an ESM transpile error from react-markdown/remark-gfm/vega, add those package names to `transpilePackages` in `next.config.mjs` and re-run.

- [ ] **Step 3: Confirm no external hosts baked in**

Run from `webapp/frontend/`:
```bash
grep -rEi "https?://[^\"' )]*(cdn|jsdelivr|unpkg|googleapis)" out/ || echo "no external runtime hosts"
```
Expected: `no external runtime hosts`. (A `vega.github.io/schema` string may appear inside example spec text; Vega does not fetch the `$schema` URL at runtime, so it is not an external dependency.)

- [ ] **Step 4: Full backend suite (unchanged, must stay green)**

Run from repo root: `.venv-agent/Scripts/python.exe -m pytest -q`
Expected: all existing backend + agent tests PASS.

- [ ] **Step 5: Commit any lockfile/build artifacts if changed**

```bash
git add -A
git commit -m "chore(console): verify static export bundles charts offline" --allow-empty
```

---

## Deployment (controller/human-run, after all tasks pass)

Not a subagent task — these hit live AWS and follow the session's changeset-preview discipline (auto-mode blocks blind applies). Run with the user's go-ahead.

**Frontend (charts UI) — no infra change:**
```bash
cd webapp/frontend && npm run build
aws s3 sync out/ s3://neat-graph-bedrock-web-site-<account_id>/ --delete
aws cloudfront create-invalidation --distribution-id E1BD6S0W7UTO9T --paths "/*"
```

**Agent prompt (SYSTEM_PROMPT) — redeploys the runtime:** the agent runs on AgentCore Runtime (container image), so a prompt change ships by rebuilding/relaunching the runtime (`agentcore launch`), not via the SAM base stack. Confirm the current runtime's deploy path before shipping. The frontend change is independent and can ship first; charts still render for any answer that already contains a spec, and the prompt change only increases how often the agent produces one.

**E2E acceptance (live console, https://<cloudfront-id>.cloudfront.net):**
Ask a trend/comparison question (e.g. "chart the temperature across my rooms right now"). Expect an interactive chart with a summary and data table beside it; Copy image and Download PNG/SVG work; a GFM table from any answer now renders as a real table, not raw pipes.

---

## Self-Review

**Spec coverage:**
- Agent emits ` ```vega-lite ` inline-data specs, auto + on request, summary+table alongside, line/bar/scatter, container width, ~200-point cap → Task 4. OK
- `MessageContent` (react-markdown + remark-gfm, no raw HTML, code renderer routes vega-lite → ChartBlock, GFM tables) → Task 2. OK
- `ChartBlock` (lazy vega-embed, parse/validate, fallback on error, light/dark theme, Copy-image + Download PNG/SVG, inline-data-only loader) → Task 1. OK
- `MessageBubble` delegates assistant body to `MessageContent`, user unchanged, animation kept → Task 3. OK
- Deps bundled, no CDN, self-contained static export → Task 5. OK
- Read-only upstream / no backend/IaC change → only files touched are frontend components + `agent/agent.py` string + tests. OK
- Testing (GFM table, fence routing, invalid-JSON fallback, export API, theme wiring, prompt-content) → Tasks 1-4 tests. OK

**Type consistency:** `ChartBlock({ spec: string })` produced in Task 1 and consumed identically in Task 2; `MessageContent({ text: string })` produced in Task 2 and consumed identically in Task 3; `MessageBubble({ message: Message })` surface unchanged, `ChatThread` untouched. OK

**Placeholder scan:** every code/test step contains complete code and exact run commands. OK

**Open risk (flagged, not blocking):** react-markdown v9 / remark-gfm v4 are ESM-only; vitest and Next 14 both handle ESM. If `npm run build` surfaces an ESM transpile error, add the packages to `transpilePackages` in `next.config.mjs` (also noted in Task 5 Step 2).
