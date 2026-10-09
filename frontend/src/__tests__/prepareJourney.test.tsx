import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// The proposal's intended user flow (Ch 3.22) is: select a restaurant, select a
// date, generate a forecast, view its uncertainty interval, compare preparation
// policies, inspect the surplus and unmet-demand figures, and inspect the
// explanation. Those steps used to live on three screens behind different roles
// — the comparison on an admin "Policy Lab", the explanation on an admin
// "Forecast Explainer" — so an operator reaching the forecast page never met
// either. These tests hold the flow together on the page they do reach, and pin
// the honesty rules that make the numbers safe to show a non-technical user:
// a future day has no realised surplus, and a missing forecast has no figure at
// all rather than an invented one.

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

let permissions: string[] = ['forecasts:read', 'policies:read', 'policies:simulate'];
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      email: 'demo@demandiq.app',
      id: 'user-1',
      initials: 'OO',
      permissions,
    },
  }),
}));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  getAuthHeader: () => ({}),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { DemandForecast } from '../pages/DemandForecast';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const TOMORROW = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

const FORECAST = {
  id: 'fc-1',
  target_date: TOMORROW,
  point_forecast: 120,
  lower_bound: 90,
  upper_bound: 160,
  coverage_target: 0.9,
  model_version_id: 'mv-abc12345',
  feature_set_version: 'fs-2026.09.2',
  fallback: false,
  created_at: '2026-09-22T04:00:00Z',
};

/** What the API returns for a day that has not happened yet. */
const SIMULATION = {
  location_id: 'loc-1',
  period_start: TOMORROW,
  period_end: TOMORROW,
  target_date: TOMORROW,
  results: [
    {
      policy: 'historical_average',
      prepared_qty: 98,
      surplus_sr: 0,
      surplus_ps: null,
      unmet_ud: null,
      planned_shortfall: 22,
      realised_demand: null,
      basis: 'forecast',
      lookback_days: 28,
      unit: 'visitor',
    },
    {
      policy: 'point_forecast',
      prepared_qty: 120,
      surplus_sr: 0,
      surplus_ps: null,
      unmet_ud: null,
      planned_shortfall: 0,
      realised_demand: null,
      basis: 'forecast',
      unit: 'visitor',
    },
    {
      policy: 'uncertainty_aware',
      prepared_qty: 153,
      surplus_sr: 33,
      surplus_ps: null,
      unmet_ud: null,
      planned_shortfall: 0,
      realised_demand: null,
      basis: 'forecast',
      quantile: 0.9,
      unit: 'visitor',
    },
  ],
};

const EXPLANATION = {
  forecast_id: 'fc-1',
  attribution_type: 'predictive',
  disclaimer: 'Predictive attribution, not causal effect.',
  contributions: [
    { feature: 'roll_mean_7', value: 118, impact: 12.4, unit: 'visitor' },
    { feature: 'lag_1', value: 130, impact: 6.1, unit: 'visitor' },
    { feature: 'is_public_holiday', value: false, impact: -9.7, unit: 'visitor' },
  ],
};

function wireApi({ withForecast = true }: { withForecast?: boolean } = {}) {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations') {
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Heights', area: 'CBD' }] });
    }
    if (path === '/api/v1/forecasts') {
      return Promise.resolve({ data: withForecast ? [FORECAST] : [], has_more: false });
    }
    if (path === '/api/v1/ingest/demand') return Promise.resolve({ data: [] });
    if (path === '/api/v1/forecasts/fc-1/explanation') return Promise.resolve(EXPLANATION);
    return Promise.resolve({ data: [] });
  });
  mockPost.mockImplementation((path: string) => {
    if (path === '/api/v1/policies/simulate') return Promise.resolve(SIMULATION);
    return Promise.resolve({});
  });
}

const simulateCalls = () =>
  mockPost.mock.calls.filter((call) => call[0] === '/api/v1/policies/simulate');

const forecastGetCalls = () => mockGet.mock.calls.map((call) => call[0]);

async function renderForecast() {
  render(<DemandForecast />);
  // Wait for the location list and its forecasts to settle.
  await waitFor(() => expect(screen.getByText('Kigali Heights')).toBeInTheDocument());
}

describe('the guided prepare journey', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permissions = ['forecasts:read', 'policies:read', 'policies:simulate'];
    wireApi();
  });

  it('asks for the restaurant, the dates and the view as numbered steps', async () => {
    await renderForecast();

    // §3.22's flow starts "select a restaurant, select a forecast date", so the
    // controls say so in those words instead of "Location / Site" / "Granularity".
    expect(screen.getByText(/Step 1 · Which restaurant\?/)).toBeInTheDocument();
    expect(screen.getByText(/Step 2 · Which dates\?/)).toBeInTheDocument();
    expect(screen.getByText(/Step 3 · How to show it/)).toBeInTheDocument();
    // More than one control can offer this label — the empty states for history
    // and for the chart each carry their own call to action, which is the right
    // behaviour for a first-time operator with no data. The step-3 control is
    // therefore asserted as present, not unique.
    expect(screen.getAllByRole('button', { name: /generate forecast/i }).length).toBeGreaterThan(0);
  });

  it('compares preparation options using the forecast on screen', async () => {
    await renderForecast();

    await waitFor(() => expect(simulateCalls().length).toBeGreaterThan(0));
    // The comparison must be built on the forecast the operator is looking at —
    // a fresh server-side guess would compare policies for a different number.
    expect(simulateCalls().at(-1)?.[1]).toMatchObject({
      location_id: 'loc-1',
      target_date: TOMORROW,
      point: 120,
      lower: 90,
      upper: 160,
      quantile: 0.9,
    });
  });

  it('leads with one plain-language answer and shows all three rules', async () => {
    await renderForecast();

    await waitFor(() => expect(screen.getByText(/portions to prepare/i)).toBeInTheDocument());
    // The suggested quantity appears in the headline and on its own card.
    expect(screen.getAllByText('153').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Your usual average')).toBeInTheDocument();
    expect(screen.getByText('Follow the forecast')).toBeInTheDocument();
    expect(screen.getByText('Forecast with a safety margin')).toBeInTheDocument();
    expect(screen.getByText('Suggested')).toBeInTheDocument();
    // The units are spelled out, because the proposal's §3.18 numbers are
    // visitor-equivalents rather than weights.
    expect(screen.getByText(/visitor-equivalents/i)).toBeInTheDocument();
  });

  it('says a surplus is not measured yet instead of reprinting the forecast', async () => {
    await renderForecast();

    // Two realised metrics per policy are unknown for tomorrow; each must say so.
    await waitFor(() => {
      expect(screen.getAllByText('not measured yet').length).toBe(6);
      // ...and the day's over-preparation is never equated with the planned buffer.
      expect(screen.getByText(/The day has not happened yet/i)).toBeInTheDocument();
    });
  });

  it('renders the day-by-day table as responsive cards for a phone', async () => {
    await renderForecast();

    // A raw 720px table was a cropped, drag-sideways table on a phone. The
    // shared table restyles each day into a labelled card below `md`, which the
    // card mode keys off `data-label`. The table only mounts once the forecast
    // list resolves, so wait for it rather than reading the DOM immediately.
    await waitFor(() => expect(document.querySelector('table.responsive-cards')).not.toBeNull());
    expect(document.querySelector('[data-label="Expected visitors"]')).not.toBeNull();
    // The model-version fragment is desktop-only.
    expect(document.querySelector('[data-card-hidden]')).not.toBeNull();
  });

  it('explains the forecast in the operator\'s words, with the causal caveat', async () => {
    await renderForecast();

    await waitFor(() => expect(screen.getByText("Last week's daily average")).toBeInTheDocument());
    expect(screen.getByText("Yesterday's customers")).toBeInTheDocument();
    expect(screen.getByText('Public holiday')).toBeInTheDocument();
    // §2.6/§3.24: a feature contribution is not a cause, and the API's own
    // wording is shown rather than paraphrased into something stronger.
    expect(screen.getByText('Predictive attribution, not causal effect.')).toBeInTheDocument();
  });
});

describe('the prepare journey respects capability', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wireApi();
  });

  it('explains the limit instead of failing for a reader without simulate', async () => {
    permissions = ['forecasts:read', 'policies:read'];
    await renderForecast();

    await waitFor(() => expect(screen.getByText(/Comparing preparation options is a decision capability/i)).toBeInTheDocument());
    expect(simulateCalls()).toHaveLength(0);
    // The forecast itself is still readable.
    expect(screen.getByText(/We expect about/i)).toBeInTheDocument();
  });

  it('hides Schedule Report from a role that cannot generate reports', async () => {
    permissions = ['forecasts:read'];
    await renderForecast();

    await waitFor(() => expect(screen.queryByRole('button', { name: /Schedule Report/i })).not.toBeInTheDocument());
  });

  it('does not read the ingestion dataset when the seat cannot ingest data', async () => {
    await renderForecast();

    // `/api/v1/ingest/demand` sits behind INGEST_WRITE. A reader asking for it
    // only produced a 403 and a chart with no history and no explanation.
    expect(forecastGetCalls()).not.toContain('/api/v1/ingest/demand');
  });

  it('reads the daily actuals when the seat may ingest data', async () => {
    permissions = ['forecasts:read', 'policies:read', 'policies:simulate', 'ingest:write'];
    await renderForecast();

    await waitFor(() => expect(forecastGetCalls()).toContain('/api/v1/ingest/demand'));
  });
});

describe('no forecast, no invented numbers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permissions = ['forecasts:read', 'policies:simulate'];
    wireApi({ withForecast: false });
  });

  it('shows an empty state rather than a fabricated confidence figure', async () => {
    render(<DemandForecast />);
    await screen.findByText('Kigali Heights');

    // A hardcoded 87% used to render here whenever there was no forecast.
    expect(screen.queryByText('87%')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/No forecast to plan from yet/i)).toBeInTheDocument());
    // Nothing to simulate, so nothing is asked of the API.
    expect(simulateCalls()).toHaveLength(0);
  });
});
