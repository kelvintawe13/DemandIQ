import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import type { ReactElement } from 'react';

/**
 * An accessibility smoke test over every screen in the app, run with axe-core.
 *
 * The point is breadth: the hand-written suites elsewhere pin the behaviours
 * they were written for, so a control that was never given an accessible name
 * (three icon-only buttons per location row, say) passes all of them. This
 * renders each page and asks a real rule engine whether the result is usable.
 *
 * Two rules are knowingly disabled, and neither is a pass:
 *  - `region`: pages are mounted here without `AppShell`, which is what
 *    supplies the `<main>` landmark in the running app.
 *  - `color-contrast`: jsdom has no layout or paint, so axe cannot compute a
 *    rendered colour and reports the rule as incomplete rather than passing.
 *    Contrast is covered instead by `contrast.test.ts`, which reads the actual
 *    design tokens out of the source.
 */

const session = {
  user: {
    name: 'Olivia Owner',
    role: 'Owner',
    roleKey: 'owner',
    email: 'demo@demandiq.app',
    id: 'user-1',
    initials: 'OO',
    mfaEnabled: true,
    phone: '+250 788 000 000',
    permissions: [
      'workspace:read',
      'forecasts:read',
      'policies:read',
      'policies:simulate',
      'reports:read',
      'alerts:read',
      'quality:read',
      'sources:read',
      'sources:write',
      'datasets:read',
      'models:read',
      'members:read',
      'billing:read',
      'apikeys:read',
      'webhooks:read',
      'agent:read',
    ],
  },
};

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ ...session, isAuthenticated: true, refresh: vi.fn(), signOut: vi.fn(), setUser: vi.fn(), clearProfileGate: vi.fn() }),
}));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../contexts/ThemeContext', () => ({ useTheme: () => ({ theme: 'light', setTheme: vi.fn(), toggleTheme: vi.fn() }) }));
vi.mock('../contexts/LocaleContext', () => ({
  useLocale: () => ({ locale: 'en', setLocale: vi.fn(), t: (key: string) => key }),
  languages: [{ code: 'en', label: 'English' }],
}));
vi.mock('../contexts/LegalContext', () => ({
  useLegal: () => ({ openLegal: vi.fn(), reviewConsent: vi.fn(), consent: true }),
}));
vi.mock('../contexts/NavigationContext', () => ({
  useNavigation: () => ({ activeId: 'overview', navigate: vi.fn() }),
  NavigationProvider: ({ children }: { children: ReactElement }) => children,
}));
vi.mock('../contexts/SiteMetaContext', () => ({
  useSiteMeta: () => ({ meta: null, refresh: vi.fn(), applyLocal: vi.fn() }),
}));
vi.mock('../contexts/AgentContext', () => ({
  useAgent: () => ({ open: false, setOpen: vi.fn(), unread: 0, clearUnread: vi.fn() }),
}));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed', code: 'failed' };
    reference = null;
  },
  getAuthHeader: () => ({}),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  apiUrl: (path: string) => path,
}));

import { api } from '../api/client';
import { Overview } from '../pages/Overview';
import { Analytics } from '../pages/Analytics';
import { DemandForecast } from '../pages/DemandForecast';
import { ImportDemandData } from '../pages/ImportDemandData';
import { Sources } from '../pages/Sources';
import { QualityMonitor } from '../pages/QualityMonitor';
import { Alerts } from '../pages/Alerts';
import { Reports } from '../pages/Reports';
import { Settings } from '../pages/Settings';
import { Profile } from '../pages/Profile';
import { Devices } from '../pages/Devices';
import { Billing } from '../pages/Billing';
import { Landing } from '../pages/Landing';
import { ErrorPage } from '../pages/ErrorPage';
import { ModelRegistry } from '../pages/admin/ModelRegistry';
import { LocationRegistry } from '../pages/admin/LocationRegistry';
import { AccessControl } from '../pages/admin/AccessControl';
import { Approvals } from '../pages/admin/Approvals';
import { DatasetsAdmin } from '../pages/admin/DatasetsAdmin';
import { AiGovernance } from '../pages/admin/AiGovernance';
import { Connectors } from '../pages/admin/Connectors';
import { Webhooks } from '../pages/admin/Webhooks';
import { PipelineHealth } from '../pages/admin/PipelineHealth';
import { PolicyLab } from '../pages/admin/PolicyLab';
import { FeatureStore } from '../pages/admin/FeatureStore';
import { Experiments } from '../pages/admin/Experiments';
import { ForecastAccuracy } from '../pages/admin/ForecastAccuracy';
import { ForecastExplainer } from '../pages/admin/ForecastExplainer';
import { SiteAdmin } from '../pages/admin/SiteAdmin';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const ISO = '2026-09-20T08:00:00Z';
const MODEL_ID = 'model-1';

/** A populated row per admin table: the tabs below render tables, and a row is
 * where the markup differs from the empty state the sweep above mounts. */
const SITE_USER = {
  id: 'user-2',
  name: 'Grace Clerk',
  email: 'clerk@demandiq.app',
  role: 'viewer',
  email_verified: true,
  deactivated_at: null,
  created_at: ISO,
  workspace_name: 'DemandIQ Demo',
};

const SITE_WORKSPACE = {
  id: 'ws-1',
  name: 'DemandIQ Demo',
  business_type: 'cafe',
  country: 'RW',
  timezone: 'Africa/Kigali',
  users: 6,
  locations: 2,
  forecasts: 14,
  created_at: ISO,
};

const MEMBER = {
  id: 'user-3',
  name: 'Samuel Analyst',
  email: 'analyst@demandiq.app',
  role: 'analyst',
  email_verified: true,
  created_at: ISO,
};

/** `GET /admin/site/settings` wraps each value as `{ value }`. */
const SITE_SETTINGS = {
  data: {
    registration_open: { value: true },
    default_role: { value: 'viewer' },
    announcement: { value: 'Reading week. Expect slower forecasts.' },
    support_email: { value: 'ops@demandiq.app' },
    maintenance_mode: { value: false },
  },
};

/**
 * Payloads for the screens that need a real shape rather than an empty list:
 * either an empty list would hide the controls under test, or the page reads
 * named fields off the response (a bare `{data: []}` would throw on render).
 */
function dataFor(path: string): unknown {
  if (path === '/api/v1/analytics/summary') {
    return {
      average_daily_demand: 412,
      average_daily_demand_change_pct: 3.1,
      total_visitors: 12_360,
      total_visitors_change_pct: 4.2,
      peak_day_date: '2026-09-18',
      peak_day_visitors: 520,
      peak_day_visitors_change_pct: -1.4,
      growth_rate: 3.6,
      growth_rate_delta_pp: 0.8,
      period: { start: '2026-09-13', end: '2026-09-19' },
      previous_period: { start: '2026-09-06', end: '2026-09-12' },
    };
  }
  if (path === '/api/v1/analytics/trend') {
    return {
      data: [{ label: '2026-09-19', current: 412, previous: 388 }],
      granularity: 'daily',
      period: { start: '2026-09-13', end: '2026-09-19' },
      previous_period: { start: '2026-09-06', end: '2026-09-12' },
    };
  }
  if (path === '/api/v1/quality/score') {
    return { overall: 88, dimensions: { completeness: 92, timeliness: 84, consistency: 90, accuracy: 86 }, open_issues: 1 };
  }
  if (path === '/api/v1/billing/plans') {
    return { data: [{ id: 'growth', name: 'Growth', price: 49, currency: 'USD', interval: 'month', features: ['Unlimited forecasts', 'Email support'] }] };
  }
  if (path === '/api/v1/billing/subscription') {
    return { plan_id: 'growth', status: 'active', current_period_end: '2026-10-20T00:00:00Z', cancel_at_period_end: false };
  }
  if (path === '/api/v1/billing/usage') {
    return { usage: [], plan: { id: 'growth', name: 'Growth' }, subscription: { plan_id: 'growth', status: 'active' } };
  }
  if (path === '/api/v1/billing/invoices') {
    return { data: [{ id: 'inv-1', number: 'INV-0001', amount: 49, currency: 'USD', status: 'paid', issued_at: ISO, download_url: 'https://files.example/inv-1.pdf' }] };
  }
  if (path === '/api/v1/billing/contact') {
    return { email: 'billing@demandiq.app', tin: 'TIN-123' };
  }
  if (path === '/api/v1/billing/payment-method') {
    return { brand: 'visa', last4: '4242', exp_month: 12, exp_year: 2027, is_default: true };
  }
  if (path === '/api/v1/locations') {
    return { data: [{ id: 'loc-1', name: 'Remera Grill', district: 'Gasabo', service_type: 'buffet', seating_capacity: 80, history_days: 42, phone: '+250 788 000 000' }], has_more: false, next_cursor: null };
  }
  if (path === '/api/v1/me/sessions') {
    // Two rows, one of them the session the request would have come with, so
    // both the badge and the sign-out control are on screen.
    return { data: [
      { id: 'sess-mine', device: 'MacBook Pro', origin: '127.0.0.1', trusted_device: false, last_seen_at: ISO, current: true },
      { id: 'sess-phone', device: 'iPhone', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO, current: false },
    ] };
  }
  if (path === '/api/v1/menu-groups') {
    return { data: [{ id: 'mg-1', name: 'Mains', share: 60, perishability: 'High', items: 12, coverage_pct: 92 }] };
  }
  if (path === '/api/v1/change-requests') {
    return { data: [{ id: 'cr-1', title: 'Promote lightgbm v4', kind: 'model_promotion', status: 'pending', requested_by: 'admin@demandiq.app', created_at: ISO, before_value: 'v3', after_value: 'v4', impact: 'MAE improves by 2.1%' }] };
  }
  if (path === '/api/v1/datasets') {
    // The real shape: `table` names where the source is stored; there is no
    // `role`.
    return { data: [{ id: 'ds-1', name: 'Demand observations', table: 'demand_observations', licence: 'Internal', records: 12_400, records_detail: '12,400 restaurant-days', coverage: '18 months', period: '2016 → 2017', status: 'validated', notes: 'Recruit restaurant records' }] };
  }
  if (path === '/api/v1/admin/agent/quotas') {
    // No `last_active` on a seat row; the counters are the API's own.
    return { data: [{ user_id: 'user-2', name: 'Grace Clerk', email: 'clerk@demandiq.app', tier: 'operator', used_today: 12, used_month: 180, limit: 60, month_limit: 600, status: 'active' }] };
  }
  if (path === '/api/v1/admin/agent/sessions') {
    // The sessions payload: a resolved name and location, a turn count and a
    // last-seen timestamp. It records no device.
    return { data: [{ id: 'sess-1', user_id: 'user-2', user_name: 'Grace Clerk', location: 'Remera Grill', started_at: ISO, last_seen_at: ISO, turn_count: 4 }] };
  }
  if (path === '/api/v1/admin/agent/guardrails') {
    return { data: [{ id: 'g-1', label: 'No invented figures', detail: 'Answers must cite a source', enabled: true, locked: true }] };
  }
  if (path === '/api/v1/admin/agent/usage') {
    return { data: [{ date: '2026-09-19', questions: 42, tool_calls: 96, refusals: 3 }, { date: '2026-09-20', questions: 51, tool_calls: 110, refusals: 1 }] };
  }
  // The model console: a registered version, the export actually being served,
  // the detail read behind it, and the runs that produce models.
  if (path === '/api/v1/models') {
    return {
      data: [
        {
          id: MODEL_ID,
          workspace_id: 'ws-1',
          algorithm: 'lightgbm',
          version: 'lightgbm-it30',
          status: 'production',
          mae: 6.7,
          rmse: 11.3,
          rmsle: 0.493,
          hyperparameters: { n_estimators: 400, learning_rate: 0.05 },
          feature_set_version: 'fs-2026.09.2',
          train_start: '2016-01-01',
          train_end: '2017-01-31',
          test_start: '2017-02-01',
          test_end: '2017-04-22',
          artifact_uri: 's3://demandiq/models/lightgbm-it30.joblib',
          trained_at: ISO,
          promoted_at: ISO,
          evaluation: {
            mase: 0.634,
            bias: -1.668,
            r2: 0.419,
            baseline_name: 'weekday_mean',
            baseline_mae: 7.667,
            mae_lift_vs_baseline: 0.126,
            test_rows: 57_866,
            role: 'champion',
            mae_delta_vs_production: null,
          },
        },
      ],
    };
  }
  if (path === `/api/v1/models/${MODEL_ID}`) {
    return {
      id: MODEL_ID,
      algorithm: 'lightgbm',
      version: 'lightgbm-it30',
      status: 'production',
      importance: [
        { feature: 'lag_7', importance: 0.22, rank: 1 },
        { feature: 'roll_mean_7', importance: 0.18, rank: 2 },
      ],
      importance_method: 'gain',
      calibration: {
        model_version_id: MODEL_ID,
        method: 'adaptive_conformal',
        target_coverage: 90,
        empirical_coverage: 89.4,
        mean_interval_width: 12,
        evaluated_at: ISO,
      },
    };
  }
  if (path === '/api/v1/models/serving') {
    return {
      mode: 'bundle',
      summary: 'trained bundle loaded: algorithm=lightgbm version=lightgbm-it30 feature_set=fs-2026.09.2',
      agreement: 'in_sync',
      note: 'The loaded export is the row marked production (lightgbm-it30).',
      bundle: {
        path: '/model/artifacts/runs/latest/serving_bundle.joblib',
        changed_at: ISO,
        exported_at: '2026-09-20T00:00:00Z',
        algorithm: 'lightgbm',
        version: 'lightgbm-it30',
        feature_set_version: 'fs-2026.09.2',
      },
      production: {
        model_version_id: MODEL_ID,
        algorithm: 'lightgbm',
        version: 'lightgbm-it30',
        feature_set_version: 'fs-2026.09.2',
        promoted_at: ISO,
      },
    };
  }
  if (path === '/api/v1/models/accuracy') {
    return {
      evaluated_at: ISO,
      window_days: 28,
      status: 'watch',
      reason: null,
      target_coverage: 90,
      reference: {
        model_version_id: MODEL_ID,
        algorithm: 'lightgbm',
        version: 'lightgbm-it30',
        test_mae: 6.702,
        test_end: '2017-04-22',
      },
      overall: { n: 18, mae: 7.5, wape_pct: 13.64, bias: 2.5, coverage_pct: 83.3, mean_actual: 55, fallback_days: 1 },
      prior: { n: 20, mae: 7.1, wape_pct: 12.9, bias: 1.9, coverage_pct: 88, mean_actual: 53, fallback_days: 0 },
      signals: [
        {
          code: 'bias',
          label: 'Forecasts run over',
          severity: 'watch',
          detail: 'On average the forecast was 2.5 visitors above a typical day of 55.',
        },
      ],
      locations: [
        {
          location_id: 'loc-1',
          name: 'Remera Grill',
          n: 18,
          mae: 7.5,
          wape_pct: 13.64,
          bias: 2.5,
          coverage_pct: 83.3,
          mean_actual: 55,
          fallback_days: 1,
          status: 'watch',
        },
      ],
    };
  }
  if (path === '/api/v1/pipeline/runs') {
    return {
      data: [
        {
          id: 'run-1',
          job_type: 'full_retrain',
          status: 'queued',
          rows_processed: null,
          duration_ms: null,
          detail: 'queued lightgbm v5 (feature set fs-2026.09.2)',
          started_at: ISO,
        },
      ],
    };
  }
  if (path === '/api/v1/alerts') {
    return { data: [{ id: 'alert-1', title: 'Big forecast miss', body: 'Nyarutarama missed by 24 customers.', severity: 'warning', created_at: ISO, read_at: null, rule_name: 'forecast_error', location_id: 'loc-1' }] };
  }
  if (path === '/api/v1/reports/schedules') {
    return { data: [{ id: 'sch-1', template: 'Weekly waste', cadence: 'weekly', recipients: ['ops@example.com'], enabled: true, next_run_at: ISO }] };
  }
  // The site-administration tabs, whose tables only exist once they have rows.
  if (path === '/api/v1/admin/site/users') {
    return { data: [SITE_USER], total: 1, has_more: false, next_cursor: null };
  }
  if (path === '/api/v1/admin/site/workspaces') {
    return { data: [SITE_WORKSPACE], total: 1, has_more: false, next_cursor: null };
  }
  if (path === '/api/v1/admin/site/settings') return SITE_SETTINGS;
  if (path === '/api/v1/admin/site/audit') {
    return {
      data: [
        {
          id: 'ev-1',
          action: 'site.user.password_reset',
          target: 'user-2',
          client: 'web',
          created_at: ISO,
        },
      ],
      total: 1,
      has_more: false,
      next_cursor: null,
    };
  }
  // Access control: members and the capability bundles the role matrix is drawn from.
  if (path === '/api/v1/members') {
    return { data: [MEMBER], has_more: false, next_cursor: null };
  }
  if (path === '/api/v1/roles') {
    return {
      data: [{ role: 'operator', label: 'Data Clerk', permissions: ['workspace:read'], assignable: true }],
      groups: [{ group: 'Workspace', capabilities: ['workspace:read'] }],
    };
  }
  return { data: [] };
}

/** Effects resolve in a couple of microtask turns; the pages with a chained
 * read (headline then detail) need the second one. */
async function settle(): Promise<void> {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function auditContainer(container: HTMLElement): Promise<string[]> {
  const results = await axe.run(container, {
    rules: {
      region: { enabled: false },
      'color-contrast': { enabled: false },
    },
  });

  return results.violations.map((violation) => {
    const where = violation.nodes.map((node) => node.html.slice(0, 160)).join('\n      ');
    return `${violation.id} (${violation.impact}): ${violation.help}\n      ${where}`;
  });
}

async function audit(ui: ReactElement): Promise<string[]> {
  const { container, unmount } = render(ui);
  await settle();
  const report = await auditContainer(container);
  unmount();
  return report;
}

beforeEach(() => {
  mockGet.mockReset();
  // Wrapped, as the real client is: a caller that chains `.catch()` on the
  // result (AccessControl, Connectors) got a TypeError from a bare object, so
  // those screens rendered their error state and were audited as if that were
  // the page.
  mockGet.mockImplementation((path: string) => Promise.resolve(dataFor(path)));
});

afterEach(() => {
  vi.clearAllMocks();
});

const OPERATOR_SCREENS: Array<[string, ReactElement]> = [
  ['Overview', <Overview onNavigate={vi.fn()} />],
  ['Analytics', <Analytics />],
  ['Demand Forecast', <DemandForecast />],
  ['Import Demand Data', <ImportDemandData />],
  ['Sources', <Sources onNavigate={vi.fn()} />],
  ['Quality Monitor', <QualityMonitor />],
  ['Alerts', <Alerts onNavigate={vi.fn()} />],
  ['Reports', <Reports />],
  ['Settings', <Settings />],
  ['Profile', <Profile />],
  ['Devices & Sessions', <Devices onNavigate={vi.fn()} />],
  ['Billing', <Billing />],
];

const ADMIN_SCREENS: Array<[string, ReactElement]> = [
  ['Model Registry', <ModelRegistry />],
  ['Forecast Accuracy', <ForecastAccuracy />],
  ['Location Registry', <LocationRegistry />],
  ['Access Control', <AccessControl />],
  ['Approvals', <Approvals />],
  ['Datasets', <DatasetsAdmin />],
  ['Assistant Governance', <AiGovernance />],
  ['Connectors', <Connectors />],
  ['Webhooks', <Webhooks />],
  ['Pipeline Health', <PipelineHealth />],
  ['Policy Lab', <PolicyLab />],
  ['Feature Store', <FeatureStore />],
  ['Experiments', <Experiments />],
  ['Forecast Explainer', <ForecastExplainer />],
  ['Site Administration', <SiteAdmin />],
];

describe('accessibility smoke test: operator screens', () => {
  it.each(OPERATOR_SCREENS)('%s has no axe violations', async (_name, ui) => {
    expect(await audit(ui)).toEqual([]);
  });
});

describe('accessibility smoke test: admin screens', () => {
  it.each(ADMIN_SCREENS)('%s has no axe violations', async (_name, ui) => {
    expect(await audit(ui)).toEqual([]);
  });
});

/**
 * Panels that only exist after a click. The render-only sweep above mounts every
 * page in its first state, so it never saw either of these: the connector test
 * run appears once Test is pressed, and the account dialog once an account is
 * opened. Both are audited here, with the payloads their endpoints return.
 */
describe('accessibility smoke test: panels behind a click', () => {
  it('the connector test run has no axe violations', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/connectors')
        return Promise.resolve({ data: [{ id: 'conn-1', name: 'Momo main', type: 'momo', status: 'active' }] });
      if (path === '/api/v1/connectors/conn-1/mapping')
        return Promise.resolve({
          mappings: [
            { id: 'm-1', source_field: 'momo.transaction_time', canonical_field: 'date', transform: 'Truncate to local day' },
          ],
        });
      // Every path resolves, including the mapping read the page chains with
      // `.catch()`: a bare value there is a TypeError, which renders offline.
      return Promise.resolve(dataFor(path));
    });
    mockPost.mockResolvedValue({
      connector_id: 'conn-1',
      latency_ms: 3,
      status: 'blocked',
      checked_at: ISO,
      checks: [
        { name: 'credentials', ok: true, detail: 'credentials stored' },
        { name: 'mapping', ok: false, detail: 'no field mapping configured' },
      ],
      sample_payload: { date: '<momo.transaction_time>', demand_qty: '<integer>' },
    });

    const { container } = render(<Connectors />);
    await settle();
    await user.click(screen.getByRole('button', { name: 'Test Momo main' }));
    await settle();

    expect(screen.getByText('Test run: Momo main')).toBeInTheDocument();
    expect(await auditContainer(container)).toEqual([]);
  });

  it('the account dialog, with a live session and its origin, has no axe violations', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/admin/site/users') {
        return Promise.resolve({
          data: [
            {
              id: 'user-2',
              name: 'Grace Clerk',
              email: 'clerk@demandiq.app',
              role: 'viewer',
              email_verified: true,
              deactivated_at: null,
              created_at: ISO,
              workspace_name: 'DemandIQ Demo',
            },
          ],
          total: 1,
        });
      }
      if (path === '/api/v1/admin/site/users/user-2') {
        return Promise.resolve({
          name: 'Grace Clerk',
          email: 'clerk@demandiq.app',
          role_label: 'Data Clerk',
          workspace: { name: 'DemandIQ Demo' },
          job_title: 'Clerk',
          phone: '+250 788 000 000',
          mfa_enabled: false,
          email_verified: true,
          created_at: ISO,
          last_seen_at: ISO,
          sessions: [
            { id: 'sess-1', device: 'iPhone', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO },
          ],
          recent_events: [
            { id: 'ev-1', action: 'POST /api/v1/me/password', target: '/api/v1/me/password', created_at: ISO, meta_data: { status_code: 200 }, client: 'web' },
          ],
        });
      }
      return Promise.resolve(dataFor(path));
    });

    const { container } = render(<SiteAdmin />);
    await settle();
    await user.click(screen.getByRole('tab', { name: 'Users' }));
    await settle();
    await user.click(await screen.findByRole('button', { name: 'View account clerk@demandiq.app' }));
    await settle();

    await waitFor(() => expect(screen.getByText(/iPhone/)).toBeInTheDocument());
    expect(await auditContainer(container)).toEqual([]);
  });
});

/**
 * The tabs. Each screen mounts on its first tab, so the sweep above never saw a
 * Site Administration audit table, the assistant's guardrails, an access-control
 * role matrix or a decided change request. Every tab is opened and audited in
 * turn, against the payloads its endpoints return.
 */
const TABBED_SCREENS: Array<[string, () => ReactElement, string[]]> = [
  ['Site Administration', () => <SiteAdmin />, ['Overview', 'Users', 'Workspaces', 'Settings', 'Audit']],
  ['Assistant Governance', () => <AiGovernance />, ['Quotas & seats', 'Sessions & limits', 'Guardrails']],
  ['Access Control', () => <AccessControl />, ['Members', 'Roles', 'Activity']],
  ['Approvals', () => <Approvals />, ['Pending', 'Decided']],
];

describe('accessibility smoke test: every tab', () => {
  it.each(TABBED_SCREENS)('%s: each tab has no axe violations', async (_name, mount, tabs) => {
    const user = userEvent.setup();
    const { container, unmount } = render(mount());
    await settle();

    for (const tab of tabs) {
      // Prefix match: a tab can carry a count in its accessible name (Approvals
      // renders "Pending 1" while a request waits).
      const name = new RegExp(`^${tab.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`);
      await user.click(screen.getByRole('tab', { name }));
      await settle();
      // The tab name travels with the report, so a violation says which tab it
      // is on rather than only which page.
      expect({ tab, violations: await auditContainer(container) }).toEqual({ tab, violations: [] });
    }
    unmount();
  });
});

describe('accessibility smoke test: signed-out screens', () => {
  it('Landing has no axe violations', async () => {
    expect(await audit(<Landing onSignIn={vi.fn()} onGetStarted={vi.fn()} />)).toEqual([]);
  });

  it('ErrorPage has no axe violations', async () => {
    expect(await audit(<ErrorPage />)).toEqual([]);
  });
});
