/**
 * A target date is a calendar day, so every part of the readout must name that
 * same day.
 *
 * The long date used to be formatted from a "UTC noon" base read in the selected
 * timezone. For a zone east of +12 that instant is already the next day: at
 * GMT+14 the strip printed `Sat · 2026-10-02` beside `Saturday, 3 October 2026`,
 * and the weekday was wrong too. The weekday now comes from the date itself and
 * the date-only formatter runs in UTC.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CalendarClock } from '../components/ui/CalendarClock';

describe('CalendarClock target date', () => {
  it('names the same calendar day everywhere it is written', () => {
    render(<CalendarClock targetDate="2026-10-02" />);

    // 2026-10-02 is a Friday.
    expect(screen.getByText(/Friday, 2 October 2026/)).toBeInTheDocument();
    expect(screen.getAllByText(/2026-10-02/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/3 October 2026/)).not.toBeInTheDocument();
  });

  it('labels the live time "computed now" only when a future day is shown', () => {
    const { unmount } = render(<CalendarClock targetDate="2026-10-02" />);
    expect(screen.getByText(/computed now/)).toBeInTheDocument();
    unmount();

    render(<CalendarClock />);
    expect(screen.queryByText(/computed now/)).not.toBeInTheDocument();
  });
});
