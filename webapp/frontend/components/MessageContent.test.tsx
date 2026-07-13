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
