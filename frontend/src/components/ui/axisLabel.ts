import type { LabelProps } from 'recharts';

/**
 * Axis titles, so every chart says what each axis measures.
 *
 * Most charts here drew tick numbers with no unit: a column of `0 35 70 105`
 * and a row of weekday names told the reader nothing about whether the value was
 * visitors, days or portions. These helpers keep the two titles — the rotated
 * one on a vertical axis and the one under a horizontal axis — in one place, so
 * a count chart, a histogram and a demand line all name their axes the same way
 * and in the same colour.
 *
 * A rotated title is drawn at the axis band's own left edge, so a chart that
 * uses `leftAxisLabel` must leave the chart a few px of left margin: with
 * `margin.left: 0` the title's ink starts left of the `<svg>` viewport, which is
 * `overflow: hidden`, and the word is clipped.
 */
const AXIS_LABEL_COLOR = '#64748b';

/** Title for a vertical axis, read bottom-to-top. */
export function leftAxisLabel(value: string): LabelProps {
  return {
    value,
    angle: -90,
    position: 'insideLeft',
    offset: 8,
    style: { fill: AXIS_LABEL_COLOR, fontSize: 11, textAnchor: 'middle' },
  };
}

/** Title for a horizontal axis, under its tick labels. */
export function bottomAxisLabel(value: string): LabelProps {
  return {
    value,
    position: 'insideBottom',
    offset: -2,
    style: { fill: AXIS_LABEL_COLOR, fontSize: 11, textAnchor: 'middle' },
  };
}
