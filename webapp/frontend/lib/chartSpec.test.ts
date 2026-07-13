import { describe, it, expect } from 'vitest';
import { parseVegaLite, seriesValues } from './chartSpec';

describe('parseVegaLite', () => {
  it('parses a simple bar spec with titles', () => {
    const m = parseVegaLite({
      mark: 'bar',
      title: 'Rooms',
      data: { values: [{ x: 'A', y: 1 }] },
      encoding: { x: { field: 'x', type: 'nominal', title: 'Cat' }, y: { field: 'y', type: 'quantitative', title: 'Count' } },
    });
    expect(m?.mark).toBe('bar');
    expect(m?.title).toBe('Rooms');
    expect(m).toMatchObject({ xField: 'x', xLabel: 'Cat', xKind: 'band', yField: 'y', yLabel: 'Count' });
  });

  it('maps mark objects and x kinds', () => {
    expect(parseVegaLite({ mark: { type: 'circle' }, data: { values: [{ a: 1, b: 2 }] }, encoding: { x: { field: 'a', type: 'quantitative' }, y: { field: 'b', type: 'quantitative' } } })?.mark).toBe('point');
    expect(parseVegaLite({ mark: 'line', data: { values: [{ a: 1, b: 2 }] }, encoding: { x: { field: 'a', type: 'temporal' }, y: { field: 'b', type: 'quantitative' } } })?.xKind).toBe('time');
    expect(parseVegaLite({ mark: 'area', data: { values: [{ a: 1, b: 2 }] }, encoding: { x: { field: 'a', type: 'quantitative' }, y: { field: 'b', type: 'quantitative' } } })?.xKind).toBe('linear');
  });

  it('detects the color/series field in first-seen order', () => {
    const m = parseVegaLite({
      mark: 'bar',
      data: { values: [{ x: 'A', y: 1, k: 'p' }, { x: 'A', y: 2, k: 'q' }, { x: 'B', y: 3, k: 'p' }] },
      encoding: { x: { field: 'x', type: 'nominal' }, y: { field: 'y', type: 'quantitative' }, color: { field: 'k' } },
    });
    expect(m?.seriesField).toBe('k');
    expect(seriesValues(m!)).toEqual(['p', 'q']);
  });

  it('returns null for unsupported mark or missing data/encoding', () => {
    expect(parseVegaLite({ mark: 'tick', data: { values: [{ x: 1, y: 2 }] }, encoding: { x: { field: 'x' }, y: { field: 'y' } } })).toBeNull();
    expect(parseVegaLite({ mark: 'bar', encoding: { x: { field: 'x' }, y: { field: 'y' } } })).toBeNull();
    expect(parseVegaLite({ mark: 'bar', data: { values: [{ x: 1 }] }, encoding: { x: { field: 'x' } } })).toBeNull();
    expect(parseVegaLite('nope')).toBeNull();
    expect(parseVegaLite(null)).toBeNull();
  });
});
