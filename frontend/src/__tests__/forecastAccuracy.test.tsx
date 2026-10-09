import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { ForecastAccuracy } from '../pages/admin/ForecastAccuracy';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const ISO = '2026-09-25T06:00:00Z';

const METRICS = {
  n: 18,
  mae: 7.5,
  wape_pct: 13.64,
  bias: 2.5,
  coverage_pct: 83.3,
  mean_actual: 55,
  fallback_days: 1,
};

const MEASURED = {
  evaluated_at: ISO,
  window_days: 28,
  status: 'degraded',
  reason: null,
  target_coverage: 90,
  reference: {
    model_version_id: 'model-1',
    algorithm: 'lightgbm',
    version: 'v4',
    test_mae: 5,
    test_end: '2026-07-28',
  },
  overall: METRICS,
  prior: { ...METRICS, n: 20, mae: 7.1, wape_pct: 12.9, bias: 1.9, coverage_pct: 88, fallback_days: 0 },
  signals: [
    {
      code: 'live_vs_test',
      label: 'Worse than the held-out window',
      severity: 'degraded',
      detail: 'Real demand is off by 7.50 a day on average, against 5.00 measured when the model was trained (50% higher).',
    },
    {
      code: 'fallback',
      label: 'Some days served by an average',
      severity: 'watch',
      detail: '1 scored day(s) were answered with a rolling-mean average instead of the model.',
    },
  ],
  locations: [
    {
      location_id: 'loc-2',
      name: 'Nyarutarama',
      n: 9,
      mae: 10,
      wape_pct: 100,
      bias: 9,
      coverage_pct: 40,
      mean_actual: 10,
      fallback_days: 1,
      status: 'degraded',
    },
    {
      location_id: 'loc-1',
      name: 'Remera Grill',
      n: 9,
      mae: 5,
      wape_pct: 5,
      bias: -1,
      coverage_pct: 100,
      mean_actual: 100,
      fallback_days: 0,
      status: 'healthy',
    },
  ],
};

const UNMEASURED = {
  ...MEASURED,
  status: 'unknown',
  overall: null,
  prior: null,
  signals: [],
  locations: [],
  reason:
    'No published forecast could be checked against recorded demand in this window yet. Accuracy appears once a forecast’s day has passed and its demand has been recorded.',
};

describe('Forecast accuracy screen', () => {
  // Clear call history per test, but reset the implementation in `afterEach`
  // rather than `beforeEach`. Vitest 1.6.x invokes a *reset* mock with no
  // arguments during its restore step, so a mock reset in `beforeEach` and then
  // given a rejecting implementation rejects again during cleanup — after the
  // assertions have already passed — and fails the test with a bare
  // "Failed to fetch". That is what made this file flap: it passed in some
  // full-suite runs and failed in others. Resetting in `afterEach` leaves the
  // mock holding no implementation when that probe runs, while `mockClear` in
  // `beforeEach` still gives every test a clean call history.
  beforeEach(() => {
    mockGet.mockClear();
  });
  afterEach(() => {
    mockGet.mockReset();
  });

  it('shows the live error beside the window before it', async () => {
    mockGet.mockResolvedValue(MEASURED);
    render(<ForecastAccuracy />);

    // The headline figures, each with the window before it for comparison.
    await waitFor(() => expect(screen.getByText('7.50 a day')).toBeInTheDocument());
    expect(screen.getByText('was 7.10')).toBeInTheDocument();
    expect(screen.getByText('13.6%')).toBeInTheDocument();
    expect(screen.getByText('was 12.9%')).toBeInTheDocument();
    // Signed bias, named in words rather than left as a number.
    expect(screen.getByText('+2.5 a day')).toBeInTheDocument();
    expect(screen.getByText('forecasts run high')).toBeInTheDocument();
    // The window-over-window move is stated, not left to the reader.
    expect(screen.getByText(/error up 0\.7 points against the window before/)).toBeInTheDocument();
    // The model's own held-out figure is the reference, with its window.
    expect(screen.getByText(/v4 \(lightgbm\) measured 5\.00 a day/)).toBeInTheDocument();
  });

  it('names each flag with the figures behind it', async () => {
    mockGet.mockResolvedValue(MEASURED);
    render(<ForecastAccuracy />);

    await waitFor(() => expect(screen.getByText('Worse than the held-out window')).toBeInTheDocument());
    expect(screen.getByText(/against 5\.00 measured when the model was trained \(50% higher\)/)).toBeInTheDocument();
    expect(screen.getByText('Some days served by an average')).toBeInTheDocument();
  });

  it('counts the days the fallback answered without scoring them', async () => {
    mockGet.mockResolvedValue(MEASURED);
    render(<ForecastAccuracy />);

    // The figure is stated where the reader is looking at the numbers it is
    // excluded from — not only in the flags panel.
    await waitFor(() => expect(screen.getByText(/1 scored day in this window were answered with a rolling-mean average instead of the model, and are left out of the figures above/)).toBeInTheDocument());
  });

  it('lists every location, worst first', async () => {
    mockGet.mockResolvedValue(MEASURED);
    render(<ForecastAccuracy />);

    await waitFor(() => expect(screen.getByText('Nyarutarama')).toBeInTheDocument());
    expect(screen.getByText('Remera Grill')).toBeInTheDocument();
    const table = screen.getByRole('table', { name: /accuracy by location/i });
    const names = Array.from(table.querySelectorAll('tbody tr')).map(
      (row) => row.querySelector('th,td')?.textContent ?? '',
    );
    expect(names[0]).toContain('Nyarutarama');

    // One row per location, so the list grows with the tenant. It is capped and
    // its header pinned, or a wide account pushes the rest of the page below
    // the fold.
    const box = table.parentElement as HTMLElement;
    expect(box.className).toContain('max-h-[420px]');
    expect(box.className).toContain('overflow-y-auto');
    expect(table.querySelector('thead th')?.className).toContain('sticky');
  });

  it('draws the in-range bar for each location', async () => {
    mockGet.mockResolvedValue(MEASURED);
    render(<ForecastAccuracy />);

    await waitFor(() => expect(screen.getByText('Nyarutarama')).toBeInTheDocument());
    const table = screen.getByRole('table', { name: /accuracy by location/i });
    // One bar per row, its width the location's own coverage — worst first, so
    // Nyarutarama (40%) precedes Remera Grill (100%).
    const bars = Array.from(table.querySelectorAll('.bg-emerald-500')) as HTMLElement[];
    expect(bars).toHaveLength(2);
    expect(bars[0].style.width).toBe('40%');
    expect(bars[1].style.width).toBe('100%');
  });

  it('says there is nothing to score rather than showing zeros', async () => {
    mockGet.mockResolvedValue(UNMEASURED);
    render(<ForecastAccuracy />);

    await waitFor(() => expect(screen.getByText('Nothing to score yet')).toBeInTheDocument());
    expect(screen.getByText(/Accuracy appears once a forecast’s day has passed/)).toBeInTheDocument();
    // A zero here would read as "the model is perfect".
    expect(screen.queryByText('0.00 a day')).toBeNull();
    expect(screen.getByText('No location has been scored')).toBeInTheDocument();
  });

  it('asks the API for the window the operator picks', async () => {
    mockGet.mockResolvedValue(MEASURED);
    const user = userEvent.setup();
    render(<ForecastAccuracy />);
    await waitFor(() => expect(screen.getByText('7.50 a day')).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText('Window'), '90');

    await vi.waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/models/accuracy', { window_days: 90 }),
    );
  });

  it('surfaces an API failure instead of an empty screen', async () => {
    // Delivered on a timer so the promise exists as *pending* when the mock
    // returns it: the component's `await` then attaches its handler before the
    // rejection fires, instead of the mock handing back an already-rejected
    // promise that has no handler attached for a tick.
    mockGet.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          setTimeout(() => reject(new TypeError('Failed to fetch')), 0);
        }),
    );
    render(<ForecastAccuracy />);

    await waitFor(() => expect(screen.getByText('You are offline')).toBeInTheDocument());
  });
});
