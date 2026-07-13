# Web Console Rich Charts — Design

**Status:** approved for planning (2026-07-10)
**Feature:** Let the agent produce rich, interactive charts (Vega-Lite) in the web
console instead of ASCII, rendered live in the browser and exportable as clean
images for reports.

## Goal

When a trend, comparison, or correlation would read better as a picture, the agent
emits a chart the console renders interactively (hover/zoom) and can export to a
PNG/SVG for pasting into a report. As a foundation, the console starts rendering
markdown properly (real tables/headings) instead of plain text.

## Global Constraints

- **Read-only upstream preserved.** No new tools, Lambdas, or upstream calls. The
  agent charts data it has *already* fetched read-only. No backend/runtime/IaC
  change — this is a frontend + agent-prompt feature.
- **Self-contained static export.** All new dependencies are bundled into the
  Next.js static export; no CDN/external hosts at runtime (consistent with the
  current CloudFront/S3 deploy).
- **Theme-aware.** Charts and markdown render correctly in the console's light and
  dark themes.
- **No new attack surface.** Rendered content is sanitized (no raw HTML) and Vega
  specs cannot load external URLs (inline data only).

## Approach (chosen)

**A — Agent emits Vega-Lite v5 specs; the browser renders them.** The agent's
answer remains a markdown string travelling the existing async path
(`POST /chat` → worker → DynamoDB → poll → frontend). That string may now contain
fenced ` ```vega-lite ` blocks holding a JSON spec with data inline. A new message
renderer parses the markdown and routes those blocks to an interactive chart
component. Because charts live in the stored answer text, they re-render on history
reload with no extra storage.

Rejected: **B** (server-side image tool — static only, adds infra/storage,
contradicts "interactive + export"); **C** (lighter lib + bespoke schema — more
custom glue, weaker export, LLM must emit our format).

## Architecture / Data Flow

```
agent.py (system prompt: emit ```vega-lite blocks when helpful)
   -> answer string (markdown + optional vega-lite fenced blocks)
   -> async worker stores answer in DynamoDB  (UNCHANGED)
   -> frontend polls, gets answer text        (UNCHANGED)
   -> MessageContent renders markdown + routes vega-lite blocks -> ChartBlock
```

Nothing in the backend, runtime contract, or data path changes.

## Components

### 1. Agent prompt — `agent/agent.py`

Add a "Visualization" section to the system prompt instructing the agent to:

- Emit a valid **Vega-Lite v5** spec inside a ` ```vega-lite ` fenced block whenever
  a trend/comparison/correlation is clearer as a chart (auto-decide; also honor
  explicit "chart that" requests).
- Put **data inline** under `data.values`; keep specs small (cap ~200 points; if
  more, aggregate/sample and say so).
- Always include a `title` and axis labels; do **not** hard-code width/height for a
  fixed pixel size — let the container size it (`"width": "container"`).
- **Always accompany a chart with (c) a short text summary and a data table** in the
  same answer, so the message is useful if a chart fails and the numbers stay
  copyable.
- Never reference external data URLs; inline data only.

Include three canonical worked examples in the prompt (kept minimal), one per
starter shape:

- **line** — a metric over time (e.g. room temperature or call jitter by timestamp).
- **bar** — a comparison across categories (e.g. avg CO₂ per room).
- **scatter** — a correlation (e.g. room temperature vs. call packet-loss).

The agent is not *limited* to these three (Vega-Lite supports more), but the prompt
steers toward them because they fit Neat sensors + Teams callRecords + ThousandEyes.

### 2. Message renderer — `webapp/frontend/components/MessageContent.tsx` (new)

- Renders an assistant message with `react-markdown` + `remark-gfm` (GFM tables,
  lists, headings, bold) — replacing today's plain-text `whitespace-pre-wrap`.
- **Raw HTML disabled** (no `rehype-raw`); only a safe component set.
- Custom `code` renderer: if the fence language is `vega-lite` (or `vega`), render
  `<ChartBlock spec={rawString} />`; otherwise render a normal preformatted code
  block.
- Tailwind-styled to match the console theme (light/dark), including table styling.
- User messages keep the current simple bubble (no markdown needed); only assistant
  messages use `MessageContent`.

### 3. Chart component — `webapp/frontend/components/ChartBlock.tsx` (new)

- **Lazy-loads** `vega-embed` via dynamic `import()` so the base bundle stays lean;
  shows a small placeholder while loading.
- Parses the spec JSON; validates it is an object. On parse/validate/render error,
  **falls back** to rendering the raw block as a code block with an inline
  "chart couldn't render" note — never throws out of the message.
- Configures Vega **theme** per the console's light/dark mode, `width: "container"`
  for responsiveness, and **disables external data loading** (Vega `loader`
  restricted / no URL fetch) so only inline data is used.
- Toolbar actions:
  - **Copy image** — PNG to clipboard via the Vega `view` API
    (`view.toCanvas()` → `toBlob` → Clipboard API).
  - **Download** — PNG and SVG via `view.toImageURL(...)`.
  - vega-embed's default action menu remains available as a fallback.
- Respects `prefers-reduced-motion`.

### 4. `MessageBubble.tsx` (modified)

- Assistant role delegates its body to `MessageContent`; user role unchanged.
- Keeps existing entrance animation + reduced-motion guard.

## Chart Vocabulary (starter examples)

Reference specs the prompt and tests use (data inline, container width):

- **Line:**
  `{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","title":"Temp over time","width":"container","data":{"values":[{"t":"2026-07-09T01:00Z","temp":21.1}, ...]},"mark":"line","encoding":{"x":{"field":"t","type":"temporal","title":"Time"},"y":{"field":"temp","type":"quantitative","title":"°C"}}}`
- **Bar:**
  `{... ,"data":{"values":[{"room":"Opotiki","co2":470}, ...]},"mark":"bar","encoding":{"x":{"field":"room","type":"nominal"},"y":{"field":"co2","type":"quantitative","title":"CO₂ ppm"}}}`
- **Scatter:**
  `{... ,"data":{"values":[{"temp":22.4,"loss":0.01}, ...]},"mark":"point","encoding":{"x":{"field":"temp","type":"quantitative"},"y":{"field":"loss","type":"quantitative","title":"Packet loss"}}}`

## Dependencies

Bundled into the static export (installed via npm; no CDN):
`react-markdown`, `remark-gfm`, `vega`, `vega-lite`, `vega-embed`. `vega-embed`
(and vega/vega-lite) are dynamically imported inside `ChartBlock` to keep the
initial bundle small.

## Error Handling

- Invalid/oversized/unsafe spec → fallback code/table + inline note; message still
  renders.
- Vega failing to load → placeholder note; rest of message unaffected.
- Malformed markdown → react-markdown degrades to text.

## Testing

Frontend (vitest):

- `MessageContent` renders a GFM table from markdown (real `<table>`).
- A ` ```vega-lite ` block mounts `ChartBlock` (vega-embed mocked); a normal code
  block does not.
- Invalid JSON in a `vega-lite` block → fallback path (no throw), note shown.
- Export button invokes the mocked Vega `view` image API.
- Theme prop wiring (light/dark config selected).

Agent:

- A prompt-content test asserting the Visualization instructions are present.
- Manual/E2E: ask for a trend in the live console → an interactive chart renders
  with a table beside it, and Copy-image/Download work.

Backend: unchanged; existing suites remain green (no backend edits).

## Out of Scope (YAGNI)

- Server-side image rendering, any new Lambda/MCP tool, or upstream changes.
- A chart-builder UI or user-editable charts.
- Dashboards / persisted chart galleries beyond the existing conversation history.
- Non-starter chart types are allowed (Vega-Lite supports them) but not specifically
  designed for or tested here.
