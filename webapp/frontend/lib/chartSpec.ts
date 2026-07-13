// Parse the *simple* Vega-Lite specs the agent emits into a flat model the
// visx renderer can draw. Anything outside the supported subset returns null,
// so ChartBlock cleanly falls back to vega-embed — nothing ever breaks.

export type Mark = 'bar' | 'line' | 'point' | 'area';
export type XKind = 'band' | 'linear' | 'time';

export type SimpleChart = {
  mark: Mark;
  title?: string;
  data: Record<string, unknown>[];
  xField: string;
  xLabel: string;
  xKind: XKind;
  yField: string;
  yLabel: string;
  seriesField?: string; // color/grouping field
};

function markOf(mark: unknown): Mark | null {
  const t = typeof mark === 'string' ? mark : (mark as { type?: string })?.type;
  if (t === 'bar') return 'bar';
  if (t === 'line') return 'line';
  if (t === 'area') return 'area';
  if (t === 'point' || t === 'circle') return 'point';
  return null;
}

function xKindOf(vlType: unknown): XKind {
  if (vlType === 'quantitative') return 'linear';
  if (vlType === 'temporal') return 'time';
  return 'band'; // nominal / ordinal / unspecified
}

type Enc = { field?: string; title?: string; type?: string } | undefined;

export function parseVegaLite(spec: unknown): SimpleChart | null {
  if (!spec || typeof spec !== 'object') return null;
  const s = spec as Record<string, unknown>;

  const mark = markOf(s.mark);
  if (!mark) return null;

  const values = (s.data as { values?: unknown })?.values;
  if (!Array.isArray(values) || values.length === 0) return null;

  const enc = s.encoding as Record<string, Enc> | undefined;
  const x = enc?.x;
  const y = enc?.y;
  if (!x?.field || !y?.field) return null;

  const color = enc?.color;
  const seriesField = color?.field;

  return {
    mark,
    title: typeof s.title === 'string' ? s.title : (s.title as { text?: string })?.text,
    data: values as Record<string, unknown>[],
    xField: x.field,
    xLabel: x.title ?? x.field,
    xKind: xKindOf(x.type),
    yField: y.field,
    yLabel: y.title ?? y.field,
    seriesField: seriesField || undefined,
  };
}

// Distinct series values (in first-seen order) for a color-grouped chart.
export function seriesValues(m: SimpleChart): string[] {
  if (!m.seriesField) return [];
  const seen: string[] = [];
  for (const row of m.data) {
    const v = String(row[m.seriesField]);
    if (!seen.includes(v)) seen.push(v);
  }
  return seen;
}
