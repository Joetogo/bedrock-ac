'use client';
import { useEffect, useRef, useState } from 'react';
import { VisxChart } from './VisxChart';
import { parseVegaLite } from '@/lib/chartSpec';

type View = {
  toCanvas: () => Promise<HTMLCanvasElement>;
  toImageURL: (type: 'png' | 'svg', scale?: number) => Promise<string>;
  finalize: () => void;
};

function triggerDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function ChartBlock({ spec }: { spec: string }) {
  // The console is a committed dark theme, so always render charts with the
  // dark Vega theme (light axes/text) — independent of the OS colour scheme.
  const dark = true;
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

  // Prefer the visx renderer for the simple specs the agent emits; fall through
  // to vega-embed for anything outside the supported subset.
  const visxModel = parsed ? parseVegaLite(parsed) : null;

  useEffect(() => {
    if (visxModel) return;                 // rendered by visx below; skip vega-embed
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
        loader.sanitize = () => Promise.reject(new Error('external resource disabled'));
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
      viewRef.current?.finalize();
      viewRef.current = null;
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

  if (visxModel) return <VisxChart model={visxModel} />;

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
