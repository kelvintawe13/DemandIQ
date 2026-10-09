/**
 * A horizontal count chart is one row per label, so its height has to follow the
 * row count.
 *
 * The access console's activity chart holds seven action names, and recharts
 * wraps a long category label onto a second line rather than dropping it: laid
 * out in a fixed 210px box, seven rows left a 30px band, a wrapped label is 25px
 * tall, and consecutive labels met at a measured **0px gap** — "Quality rule
 * switched" sitting directly on "Policy comparison run". 34px per row fixes it.
 *
 * The height is the whole assertion here: it is what the reader sees, and it is
 * the value that made the labels collide. (`labelWidth`, the other half of the
 * fix, is a recharts axis width and can only be measured against a real layout —
 * the browser probe for `/admin-access` asserts that no tick is drawn outside its
 * own `<svg>`, which is the clipping it prevents.)
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { CountChart } from '../components/ui/CountChart';

const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ label: `Label ${i}`, count: i + 1 }));

/** The box the chart is drawn in — the element that carries the height. */
const box = (container: HTMLElement) => container.firstElementChild as HTMLElement;

describe('CountChart sizing', () => {
  it('gives every horizontal bar its own row', () => {
    const { container } = render(<CountChart data={rows(7)} layout="bars" />);
    expect(box(container).style.height).toBe('238px');
  });

  it('leaves a short list the height it always had', () => {
    // Two devices in one restaurant must not become a taller panel than before.
    const { container } = render(<CountChart data={rows(2)} layout="bars" />);
    expect(box(container).style.height).toBe('210px');
    // Five is still under the old fixed height.
    const { container: five } = render(<CountChart data={rows(5)} layout="bars" />);
    expect(box(five).style.height).toBe('210px');
  });

  it('keeps columns at a fixed height, where the row count is the x-axis', () => {
    const { container } = render(<CountChart data={rows(7)} />);
    expect(box(container).style.height).toBe('210px');
    const { container: many } = render(<CountChart data={rows(20)} />);
    expect(box(many).style.height).toBe('210px');
  });

  it('stops growing at fourteen rows, and says so in the source', () => {
    // Past the cap the panel is a table; callers with an unbounded list bound it
    // themselves (the access console folds its tail into "Other (n)").
    const { container } = render(<CountChart data={rows(30)} layout="bars" />);
    expect(box(container).style.height).toBe('476px');
  });

  it('still draws nothing, rather than an empty axis, with no data', () => {
    const { container } = render(
      <CountChart data={[]} layout="bars" emptyTitle="Nothing to show" emptyDescription="No rows" />,
    );
    expect(container.textContent).toContain('Nothing to show');
  });
});
