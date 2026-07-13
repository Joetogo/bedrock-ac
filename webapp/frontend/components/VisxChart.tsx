'use client';
import { useMemo, useRef } from 'react';
import {
  XYChart, AnimatedAxis, AnimatedGrid, BarSeries, BarGroup, LineSeries, AreaSeries,
  GlyphSeries, Tooltip, buildChartTheme,
} from '@visx/xychart';
import { ParentSize } from '@visx/responsive';
import type { SimpleChart } from '@/lib/chartSpec';
import { seriesValues } from '@/lib/chartSpec';

const PALETTE = ['#5b9dff', '#f4b23f', '#37d39a', '#b98cff', '#ff7a9c', '#4dd0e1'];
const MONO = 'var(--ngb-mono)';

const theme = buildChartTheme({
  backgroundColor: 'transparent',
  colors: PALETTE,
  gridColor: 'rgba(130,160,255,0.16)',
  gridColorDark: 'rgba(130,160,255,0.08)',
  tickLength: 4,
  svgLabelSmall: { fill: '#8b95ab', fontFamily: MONO, fontSize: 10 },
  svgLabelBig: { fill: '#8b95ab', fontFamily: MONO, fontSize: 11, fontWeight: 500 },
  xAxisLineStyles: { stroke: 'rgba(130,160,255,0.18)' },
  yAxisLineStyles: { stroke: 'transparent' },
  xTickLineStyles: { stroke: 'rgba(130,160,255,0.18)' },
  yTickLineStyles: { stroke: 'transparent' },
});

const num = (v: unknown) => (typeof v === 'number' ? v : Number(v));

function triggerDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
}

export function VisxChart({ model }: { model: SimpleChart }) {
  const host = useRef<HTMLDivElement | null>(null);
  const m = model;
  const series = seriesValues(m);
  const multi = series.length > 0;

  const xAcc = (d: Record<string, unknown>) =>
    m.xKind === 'time' ? new Date(String(d[m.xField]))
    : m.xKind === 'linear' ? num(d[m.xField])
    : String(d[m.xField]);
  const yAcc = (d: Record<string, unknown>) => num(d[m.yField]);

  const xScale = (m.xKind === 'band'
    ? { type: 'band', paddingInner: 0.3 }
    : m.xKind === 'time' ? { type: 'time' } : { type: 'linear' }) as never;

  const bySeries = useMemo(() => {
    if (!m.seriesField) return {};
    const map: Record<string, Record<string, unknown>[]> = {};
    for (const s of series) map[s] = [];
    for (const row of m.data) map[String(row[m.seriesField])]?.push(row);
    return map;
  }, [m, series]);

  const rotate = m.xKind !== 'linear' && m.data.length > 5;
  const tickLabelProps = () => ({
    fill: '#8b95ab', fontFamily: MONO, fontSize: 9,
    ...(rotate ? { angle: -30, textAnchor: 'end' as const, dy: '0.1em' } : {}),
  });

  const renderSeries = () => {
    const single = m.data;
    if (m.mark === 'bar') {
      return multi ? (
        <BarGroup>
          {series.map((s) => (
            <BarSeries key={s} dataKey={s} data={bySeries[s] ?? []} xAccessor={xAcc} yAccessor={yAcc} />
          ))}
        </BarGroup>
      ) : (
        <BarSeries dataKey={m.yLabel} data={single} xAccessor={xAcc} yAccessor={yAcc} />
      );
    }
    if (m.mark === 'area') {
      return multi
        ? series.map((s) => <AreaSeries key={s} dataKey={s} data={bySeries[s] ?? []} xAccessor={xAcc} yAccessor={yAcc} fillOpacity={0.25} />)
        : <AreaSeries dataKey={m.yLabel} data={single} xAccessor={xAcc} yAccessor={yAcc} fillOpacity={0.25} />;
    }
    if (m.mark === 'point') {
      return multi
        ? series.map((s) => <GlyphSeries key={s} dataKey={s} data={bySeries[s] ?? []} xAccessor={xAcc} yAccessor={yAcc} size={42} />)
        : <GlyphSeries dataKey={m.yLabel} data={single} xAccessor={xAcc} yAccessor={yAcc} size={42} />;
    }
    // line
    return multi
      ? series.map((s) => <LineSeries key={s} dataKey={s} data={bySeries[s] ?? []} xAccessor={xAcc} yAccessor={yAcc} />)
      : <LineSeries dataKey={m.yLabel} data={single} xAccessor={xAcc} yAccessor={yAcc} />;
  };

  const exportSvg = () => {
    const svg = host.current?.querySelector('svg');
    if (!svg) return;
    const clone = svg.cloneNode(true) as SVGElement;
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const str = new XMLSerializer().serializeToString(clone);
    triggerDownload(URL.createObjectURL(new Blob([str], { type: 'image/svg+xml' })), 'chart.svg');
  };

  const toPng = (cb: (blob: Blob) => void) => {
    const svg = host.current?.querySelector('svg');
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const str = new XMLSerializer().serializeToString(svg);
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = Math.max(1, rect.width * 2); c.height = Math.max(1, rect.height * 2);
      const ctx = c.getContext('2d'); if (!ctx) return;
      ctx.scale(2, 2);
      ctx.fillStyle = '#0f1524'; ctx.fillRect(0, 0, rect.width, rect.height);
      ctx.drawImage(img, 0, 0, rect.width, rect.height);
      c.toBlob((b) => { if (b) cb(b); }, 'image/png');
    };
    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(str)));
  };

  const downloadPng = () => toPng((b) => triggerDownload(URL.createObjectURL(b), 'chart.png'));
  const copyPng = () => toPng(async (b) => {
    try {
      if (navigator.clipboard && 'write' in navigator.clipboard) {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]);
      }
    } catch { /* clipboard unavailable */ }
  });

  return (
    <figure className="my-3">
      {m.title && (
        <figcaption className="mb-1 text-center text-xs" style={{ color: 'var(--muted)', fontFamily: MONO }}>
          {m.title}
        </figcaption>
      )}
      {multi && (
        <div className="mb-1 flex flex-wrap justify-center gap-4 text-xs" style={{ color: 'var(--muted)' }}>
          {series.map((s, i) => (
            <span key={s} className="inline-flex items-center gap-1.5" style={{ fontFamily: MONO }}>
              <i style={{ width: 10, height: 10, borderRadius: 3, background: PALETTE[i % PALETTE.length], display: 'inline-block' }} />
              {s}
            </span>
          ))}
        </div>
      )}
      <div ref={host} style={{ width: '100%', height: 300 }}>
        <ParentSize>{({ width, height }) => (width > 0 && height > 0 ? (
        <XYChart
          width={width}
          height={height}
          xScale={xScale}
          yScale={{ type: 'linear', nice: true } as never}
          theme={theme}
          margin={{ top: 14, right: 18, bottom: rotate ? 66 : 46, left: 52 }}
        >
          <AnimatedGrid columns={false} numTicks={4} />
          <AnimatedAxis orientation="left" label={m.yLabel} numTicks={4} labelProps={{ fill: '#59637a', fontFamily: MONO, fontSize: 10 }} />
          <AnimatedAxis orientation="bottom" numTicks={Math.min(m.data.length, 8)} tickLabelProps={tickLabelProps} />
          {renderSeries()}
          <Tooltip
            snapTooltipToDatumX
            snapTooltipToDatumY
            showVerticalCrosshair
            showSeriesGlyphs={multi}
            renderTooltip={({ tooltipData }) => {
              const near = tooltipData?.nearestDatum?.datum as Record<string, unknown> | undefined;
              if (!near) return null;
              return (
                <div style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.5 }}>
                  <div style={{ color: '#0b1020' }}>{String(near[m.xField])}</div>
                  <div style={{ color: '#0b1020', fontWeight: 600 }}>{m.yLabel}: {num(near[m.yField])}</div>
                  {m.seriesField && <div style={{ color: '#4b5563' }}>{String(near[m.seriesField])}</div>}
                </div>
              );
            }}
          />
        </XYChart>
        ) : null)}</ParentSize>
      </div>
      <div className="mt-1 flex justify-end gap-2">
        {[['Copy image', copyPng], ['Download PNG', downloadPng], ['Download SVG', exportSvg]].map(([label, fn]) => (
          <button
            key={label as string}
            type="button"
            onClick={fn as () => void}
            className="rounded-md border px-2 py-1 text-xs"
            style={{ borderColor: 'var(--border-2)', color: 'var(--muted)', fontFamily: MONO }}
          >
            {label as string}
          </button>
        ))}
      </div>
    </figure>
  );
}
