import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// The Analytics screen is a stack of filters over five endpoints. Every filter
// used to be decorative — the location picker offered invented branches and was
// never sent, the Daily/Weekly control refetched the same daily series, and a
// custom range silently fell back to 30 days. These tests assert on the request
// each control produces, because "the control looks like it works" was exactly
// the failure.

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      email: 'demo@demandiq.app',
      id: 'owner-1',
      initials: 'OO',
      permissions: ['analytics:read'],
    },
  }),
}));

const toast = vi.fn();
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { Analytics } from '../pages/Analytics';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

const PERIOD = { start: '2026-08-23', end: '2026-09-21' };
const PREVIOUS = { start: '2026-07-25', end: '2026-08-22' };

function summary() {
  return {
    period: PERIOD,
    previous_period: PREVIOUS,
    total_visitors: 4820,
    total_visitors_previous: 4310,
    total_visitors_change_pct: 11.8,
    average_daily_demand: 161,
    average_daily_demand_previous: 144,
    average_daily_demand_change_pct: 11.8,
    peak_day_visitors: 268,
    peak_day_visitors_previous: 240,
    peak_day_visitors_change_pct: 11.7,
    peak_day_date: '2026-09-16',
    growth_rate: 11.8,
    growth_rate_delta_pp: 0,
  };
}

function breakdown() {
  return {
    data: [
      {
        date: '2026-09-21',
        location_name: 'Kigali Heights',
        visitors: 1820,
        visitors_formatted: '1,820',
        previous: 1601,
        previous_formatted: '1,601',
        change: '13.7%',
        change_pct: 13.7,
        isPositive: true,
        spark: [120, 140, 160, 182],
      },
      {
        date: '2026-09-21',
        location_name: 'CBD Canteen',
        visitors: 1204,
        visitors_formatted: '1,204',
        previous: 1320,
        previous_formatted: '1,320',
        change: '-8.8%',
        change_pct: -8.8,
        isPositive: false,
        spark: [150, 140, 130, 120],
      },
    ],
    has_more: false,
    next_cursor: null,
  };
}

/** Every call the screen makes, so a test can assert on what was sent. */
let calls: { path: string; params: Record<string, unknown> }[] = [];

function wireApi() {
  calls = [];
  mockGet.mockImplementation((path: string, params: Record<string, unknown> = {}) => {
    calls.push({ path, params });
    if (path === '/api/v1/locations') {
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Heights' }, { id: 'loc-2', name: 'CBD Canteen' }] });
    }
    if (path === '/api/v1/analytics/summary') return Promise.resolve(summary());
    if (path === '/api/v1/analytics/trend') {
      return Promise.resolve({
        data: [
          { label: '2026-09-15', current: 132, previous: 118 },
          { label: '2026-09-16', current: 171, previous: 144 },
        ],
        granularity: (params.granularity as string) ?? 'daily',
        period: PERIOD,
        previous_period: PREVIOUS,
      });
    }
    if (path === '/api/v1/analytics/by-location') {
      return Promise.resolve({
        data: [
          { name: 'Kigali Heights', share: '37.8%', visitors: 1820, visitors_formatted: '1,820', value: 37.8, color: '#0f766e', rank: 1 },
          { name: 'CBD Canteen', share: '25.0%', visitors: 1204, visitors_formatted: '1,204', value: 25, color: '#0ea5e9', rank: 2 },
        ],
      });
    }
    if (path === '/api/v1/analytics/breakdown') return Promise.resolve(breakdown());
    if (path === '/api/v1/analytics/insights') {
      return Promise.resolve({ data: [{ message: 'Friday demand is up 9% over four weeks.', type: 'trend' }] });
    }
    return Promise.resolve({ data: [] });
  });
}

const paramsFor = (path: string) => calls.filter((c) => c.path === path).map((c) => c.params);

async function renderAnalytics() {
  render(<Analytics />);
  await screen.findByRole('heading', { name: 'Performance Breakdown' });
}

describe('Analytics filters reach the API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wireApi();
  });

  it('offers the workspace locations, not invented branches', async () => {
    const user = userEvent.setup();
    await renderAnalytics();

    await user.click(screen.getByRole('button', { name: /^Location:/ }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('button', { name: 'Kigali Heights' })).toBeInTheDocument();
    // These were hardcoded in the picker's fallback list and belong to no
    // workspace, so choosing one filtered by a name the API has never heard of.
    expect(within(dialog).queryByRole('button', { name: 'Kimironi Branch' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Nyarutarama Cafe' })).not.toBeInTheDocument();
  });

  it('sends location_id once a single location is chosen', async () => {
    const user = userEvent.setup();
    await renderAnalytics();

    expect(paramsFor('/api/v1/analytics/summary')[0]).not.toHaveProperty('location_id');

    await user.click(screen.getByRole('button', { name: /^Location:/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Kigali Heights' }));
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(paramsFor('/api/v1/analytics/summary').at(-1)).toMatchObject({ location_id: 'loc-1' }));
    // Trend and breakdown are scoped too: the trend line and the detail rows
    // would otherwise describe a different population than the KPIs above them.
    expect(paramsFor('/api/v1/analytics/trend').at(-1)).toMatchObject({ location_id: 'loc-1' });
    expect(paramsFor('/api/v1/analytics/breakdown').at(-1)).toMatchObject({ location_id: 'loc-1' });
    // Share of demand is only meaningful across the group.
    expect(paramsFor('/api/v1/analytics/by-location').at(-1)).not.toHaveProperty('location_id');

    await waitFor(() => expect(screen.getByText(/Scoped to/)).toBeInTheDocument());
  });

  it('sends granularity so Weekly is not a no-op', async () => {
    const user = userEvent.setup();
    await renderAnalytics();

    expect(paramsFor('/api/v1/analytics/trend')[0]).toMatchObject({ granularity: 'daily' });

    await user.click(screen.getByRole('button', { name: 'Weekly' }));
    await waitFor(() => expect(paramsFor('/api/v1/analytics/trend').at(-1)).toMatchObject({ granularity: 'weekly' }));
  });

  it('sends real bounds for a custom range instead of a 30-day fallback', async () => {
    const user = userEvent.setup();
    await renderAnalytics();

    await user.click(screen.getByRole('button', { name: /^Period:/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Custom range' }));

    const from = within(dialog).getByLabelText('From') as HTMLInputElement;
    const to = within(dialog).getByLabelText('To') as HTMLInputElement;
    // The defaults used to be a frozen pair of dates that went stale.
    expect(from.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(to.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    await user.clear(from);
    await user.type(from, '2026-06-01');
    await user.clear(to);
    await user.type(to, '2026-06-30');
    await user.click(within(dialog).getByRole('button', { name: /Apply range/ }));

    await waitFor(() => {
      const last = paramsFor('/api/v1/analytics/summary').at(-1) ?? {};
      expect(last).toMatchObject({ from: '2026-06-01', to: '2026-06-30' });
      // A stale preset must not travel alongside the bounds.
      expect(last).not.toHaveProperty('range');
    });

    // And the active range is visible, with a way out of it.
    await user.click(screen.getByRole('button', { name: /Clear custom range/ }));
    await waitFor(() => expect(paramsFor('/api/v1/analytics/summary').at(-1)).toMatchObject({ range: '30d' }));
  });

  it('names the breakdown rows for what they are', async () => {
    const user = userEvent.setup();
    await renderAnalytics();

    // Unscoped, the API aggregates per location and stamps every row with the
    // period end date — labelling that column "Date" printed one date N times.
    const headers = () => screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers()[0]).toBe('Location');
    // The location is the row header: the first column names its row, so a
    // screen reader can attach the row's values to it.
    expect(screen.getAllByRole('rowheader', { name: 'Kigali Heights' }).length).toBeGreaterThan(0);
    expect(screen.queryAllByRole('cell', { name: '2026-09-21' }).length).toBe(0);

    await user.click(screen.getByRole('button', { name: /^Location:/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'CBD Canteen' }));
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }));

    // Scoped, the rows really are days, so the column says Date.
    await waitFor(() => expect(headers()[0]).toBe('Date'));
  });

  it('does not offer metrics the endpoint cannot answer', async () => {
    await renderAnalytics();
    // `GET /analytics/by-location` takes no metric parameter, so this selector
    // returned identical numbers for two of its three options.
    expect(screen.queryByText('Surplus exposure')).not.toBeInTheDocument();
    expect(screen.queryByText('Unmet demand')).not.toBeInTheDocument();
  });
});
