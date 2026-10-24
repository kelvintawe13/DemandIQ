import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// A mutable seat, so a test can hand the same screen a clerk's capabilities and
// then an analyst's without re-mocking the module.
const session: { user: Record<string, unknown> } = {
  user: {
    name: 'Grace Clerk',
    role: 'Data Clerk',
    roleKey: 'operator',
    email: 'clerk@demandiq.app',
    id: 'clerk-1',
    initials: 'GC',
    mfaEnabled: false,
    phone: '+250 788 111 222',
    permissions: ['workspace:read', 'reports:read', 'alerts:read', 'alerts:write', 'quality:read', 'quality:fix', 'sources:read', 'sources:write'],
  },
};

vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../contexts/ThemeContext', () => ({ useTheme: () => ({ theme: 'light', setTheme: vi.fn() }) }));
vi.mock('../contexts/LocaleContext', () => ({
  useLocale: () => ({ locale: 'en', setLocale: vi.fn(), t: (key: string) => key }),
  languages: [{ code: 'en', label: 'English' }],
}));
vi.mock('../contexts/LegalContext', () => ({
  useLegal: () => ({ openLegal: vi.fn(), reviewConsent: vi.fn(), consent: true }),
}));
// A stable spy, so a test can assert where a screen hands the reader off to.
const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('../contexts/NavigationContext', () => ({
  useNavigation: () => ({ activeId: 'admin-policies', navigate, back: vi.fn(), canGoBack: false }),
}));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  // The real helper prefixes VITE_API_URL; unset in tests, so a path stays one.
  apiUrl: (path: string) => path,
}));

import { api } from '../api/client';
import { Reports } from '../pages/Reports';
import { Alerts } from '../pages/Alerts';
import { QualityMonitor } from '../pages/QualityMonitor';
import { Sources } from '../pages/Sources';
import { Settings } from '../pages/Settings';
import { Profile } from '../pages/Profile';
import { Billing } from '../pages/Billing';
import { PolicyLab } from '../pages/admin/PolicyLab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPatch = api.patch as unknown as ReturnType<typeof vi.fn>;

const ISO = '2026-09-20T08:00:00Z';

function routeGet(path: string) {
  if (path === '/api/v1/reports/templates') {
    return Promise.resolve({ data: [{ id: 'tpl-1', name: 'Weekly waste', description: 'Waste vs demand', cadence: 'Weekly' }] });
  }
  if (path === '/api/v1/reports/schedules') {
    return Promise.resolve({
      data: [{ id: 'sch-1', template: 'Weekly waste', cadence: 'weekly', recipients: ['ops@example.com'], enabled: true, next_run_at: ISO }],
    });
  }
  if (path === '/api/v1/reports') {
    return Promise.resolve({
      data: [
        { id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3', status: 'completed', format: 'pdf', created_at: ISO },
      ],
    });
  }
  if (path === '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3') {
    return Promise.resolve({
      id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3',
      template: 'demand_summary',
      period_start: '2026-08-20T00:00:00Z',
      period_end: '2026-09-19T00:00:00Z',
      format: 'pdf',
      status: 'completed',
      signed_url: 'https://files.example/report.pdf',
      download_url: 'https://files.example/report.pdf',
      size_bytes: 248_320,
      created_at: ISO,
    });
  }
  if (path === '/api/v1/alerts') {
    return Promise.resolve({
      data: [
        {
          id: 'alert-1',
          title: 'Coverage below 90%',
          body: 'Nyarutarama coverage fell to 84% on 2026-09-19.',
          severity: 'warning',
          created_at: ISO,
          read_at: null,
          resolved_at: null,
          rule_name: 'coverage_breach',
          location_id: 'loc-1',
        },
      ],
    });
  }
  if (path === '/api/v1/alerts/rules') {
    return Promise.resolve({ data: [{ id: 'rule-1', name: 'coverage_breach', description: 'Alerts when coverage drops', enabled: true, channel: 'all channels' }] });
  }
  if (path === '/api/v1/quality/score') {
    return Promise.resolve({ overall: 88, dimensions: { completeness: 92, timeliness: 84 }, open_issues: 1 });
  }
  if (path === '/api/v1/quality/issues') {
    return Promise.resolve({
      data: [
        {
          id: 'issue-1',
          summary: 'Missing demand for 3 days',
          severity: 'warning',
          created_at: ISO,
          location_id: 'loc-1',
          suggested_fix: 'Backfill from POS export',
        },
      ],
    });
  }
  if (path === '/api/v1/quality/rules') {
    return Promise.resolve({ data: [{ id: 'qrule-1', name: 'gap_detection', description: 'Detects gaps in a demand series', enabled: true }] });
  }
  if (path === '/api/v1/locations') {
    return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Heights Bistro' }] });
  }
  if (path === '/api/v1/sources') {
    return Promise.resolve({
      data: [
        {
          id: 'src-1',
          name: 'Kigali POS',
          type: 'pos',
          status: 'connected',
          last_sync_at: ISO,
          rows: 1840,
          freshness: '12 minutes ago',
        },
        {
          id: 'src-2',
          name: 'OCR Batch',
          type: 'ocr',
          status: 'available',
          // A never-synced channel. `typeof null === 'object'`, so a naive
          // render filter drops this field entirely.
          last_sync_at: null,
          volume: 0,
        },
      ],
    });
  }
  if (path === '/api/v1/sources/sync-history') {
    return Promise.resolve({ data: [{ id: 'run-1', source: 'Kigali POS', status: 'success', started_at: ISO, rows_processed: 1840 }] });
  }
  if (path === '/api/v1/audit-log') {
    // The real shape: `action` is the request line, `target` the same path, and
    // the outcome/surface live in `meta_data`/`client`
    // (backend/app/middleware/audit.py).
    return Promise.resolve({
      data: [
        {
          id: 'aud-1',
          action: 'PATCH /api/v1/me/preferences',
          target: '/api/v1/me/preferences',
          client: 'web',
          meta_data: { method: 'PATCH', path: '/api/v1/me/preferences', status_code: 200 },
          created_at: ISO,
        },
      ],
    });
  }
  if (path === '/api/v1/pipeline/runs') {
    return Promise.resolve({
      data: [
        {
          id: 'run-1',
          job_type: 'connector_sync',
          status: 'success',
          rows_processed: 12,
          detail: 'synced 2 active connector(s)',
          started_at: ISO,
        },
      ],
    });
  }
  if (path === '/api/v1/policies') {
    return Promise.resolve({
      data: [
        { id: 'pol-1', kind: 'uncertainty_aware', quantile: 0.85, lookback_days: 28, is_active: true },
        { id: 'pol-2', kind: 'risk_aware', quantile: null, lookback_days: 28, risk_weight: 2, is_active: false },
      ],
    });
  }
  if (path === '/api/v1/billing/status') {
    return Promise.resolve({ enabled: true, provider: 'stripe', mode: 'test', currency: 'RWF' });
  }
  if (path === '/api/v1/billing/plans') {
    return Promise.resolve({
      data: [
        { id: 'starter', name: 'Starter', price: 'RWF 0', cadence: 'forever', blurb: 'One location.', features: ['1 location'] },
        { id: 'growth', name: 'Growth', price: 'RWF 48,000', cadence: 'per month', blurb: 'Multi-site forecasting.', features: ['Up to 10 locations'] },
      ],
    });
  }
  if (path === '/api/v1/billing/subscription') {
    return Promise.resolve({ id: 'sub-1', workspace_id: 'ws-1', plan: null, plan_id: 'starter', status: 'active', current_period_start: ISO, current_period_end: ISO, cancel_at_period_end: false, trial_end: null });
  }
  if (path === '/api/v1/me/identities') {
    // The real shape: one row per linked provider, carrying when it was linked.
    // The provider's stable subject id is left out on purpose: the
    // unread-fields gate counts a key as read if its name appears anywhere in
    // `src`, so a fixture would satisfy it without any screen rendering it.
    return Promise.resolve([
      { provider: 'google', linked_at: '2026-08-14T09:00:00Z' },
    ]);
  }
  if (path === '/api/v1/me/sessions') {
    // The shape `GET /api/v1/me/sessions` returns: device, origin,
    // trusted_device, last_seen_at, and `current` on the row the request's own
    // token belongs to. No `created_at`.
    return Promise.resolve({
      data: [
        { id: 'sess-mine', device: 'MacBook Pro', origin: '127.0.0.1', trusted_device: false, last_seen_at: ISO, current: true },
        { id: 'sess-phone', device: 'iPhone', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO, current: false },
      ],
    });
  }
  if (path === '/api/v1/billing/usage') {
    return Promise.resolve({ usage: [], plan: { id: 'starter', name: 'Starter' }, subscription: { plan_id: 'starter', status: 'active' } });
  }
  if (path === '/api/v1/billing/invoices') {
    return Promise.resolve({ data: [] });
  }
  if (path === '/api/v1/billing/contact') {
    return Promise.resolve({ email: 'billing@demandiq.app', tin: 'TIN-123' });
  }
  if (path === '/api/v1/billing/payment-method') {
    return Promise.resolve({ brand: 'visa', last4: '4242', exp_month: 12, exp_year: 2027, is_default: true });
  }
  if (path === '/api/v1/workspace') {
    return Promise.resolve({
      id: 'ws-1',
      name: 'DemandIQ Demo',
      business_type: 'cafe',
      country: 'RW',
      timezone: 'Africa/Kigali',
      created_at: ISO,
      counts: { users: 6, locations: 2, forecasts: 14 },
      can_manage: false,
    });
  }
  return Promise.resolve({ data: [] });
}

beforeEach(() => {
  session.user.permissions = [
    'workspace:read',
    'reports:read',
    'alerts:read',
    'alerts:write',
    'quality:read',
    'quality:fix',
    'sources:read',
    'sources:write',
  ];
  mockGet.mockReset();
  mockGet.mockImplementation(routeGet);
  mockPatch.mockReset();
  mockPatch.mockResolvedValue({});
  navigate.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  // The report poller runs on an interval; a test that faked timers has to hand
  // the real clock back or every later test inherits them.
  vi.useRealTimers();
});

/* ------------------------------------------------------------------- reports */

describe('Reports as a Data Clerk', () => {
  it('explains that generating and scheduling need a seat the clerk does not have', async () => {
    render(<Reports />);
    const generate = await screen.findByRole('button', { name: /generate pdf/i });
    expect(generate).toBeDisabled();
    expect(screen.getByRole('button', { name: /schedule instead/i })).toBeDisabled();
    expect(screen.getByText(/can read reports but not create them/i)).toBeInTheDocument();

    // The library and history stay readable.
    expect(screen.getAllByText('Weekly waste').length).toBeGreaterThan(0);
    expect(screen.getByText('ops@example.com')).toBeInTheDocument();
  });

  it('offers the same controls to an analyst who holds reports:generate', async () => {
    session.user.permissions = ['reports:read', 'reports:generate'];
    render(<Reports />);
    await waitFor(() => expect(screen.getByRole('button', { name: /generate pdf/i })).toBeEnabled());
    expect(screen.getByRole('button', { name: /schedule instead/i })).toBeEnabled();
  });

  it('turns the period into a working custom range instead of a dead option', async () => {
    const user = userEvent.setup();
    session.user.permissions = ['reports:read', 'reports:generate'];
    render(<Reports />);

    // The picker opens with real presets and a custom range.
    await user.click(await screen.findByRole('button', { name: /last 30 days/i }));
    await waitFor(() => expect(screen.getByText('Date range')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Custom range' }));

    // The bounds the user is shown are the ones that get applied — read them
    // off the inputs rather than re-deriving the default window, which is
    // relative to today (it used to be a frozen pair of dates).
    const from = (screen.getByLabelText('From') as HTMLInputElement).value;
    const to = (screen.getByLabelText('To') as HTMLInputElement).value;
    expect(from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await user.click(screen.getByRole('button', { name: /apply range/i }));

    // The chosen range is now what "Generate" will send — the period button
    // carries it, and the label is the custom one, not a preset.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: new RegExp(`${from} → ${to}`) })).toBeInTheDocument(),
    );
  });

  it('opens a generated report in a detail view instead of dead-ending on Download', async () => {
    // The list row carries only an id, so the old "Download" had no URL to open
    // and could only toast a status at the reader.
    const user = userEvent.setup();
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Open report / }));

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByText(/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3/)).toBeInTheDocument());
    // The API keeps templates as keys; the detail view shows them as words.
    expect(within(dialog).getAllByText('Demand summary').length).toBeGreaterThan(0);
    // Status is shown in plain words, not the raw API token.
    expect(within(dialog).getByText('Completed')).toBeInTheDocument();
    // `uppercase` is a CSS transform, so the DOM text stays as the API sent it.
    expect(within(dialog).getByText('pdf')).toBeInTheDocument();
    expect(within(dialog).getByText('242.5 KB')).toBeInTheDocument();
    // The footer reflects the real file state.
    expect(within(dialog).getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('says the file is pending when a report has no download yet', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3'
        ? Promise.resolve({ id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3', format: 'pdf', status: 'queued', size_bytes: null })
        : routeGet(path),
    );
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Open report / }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByText('Waiting to start')).toBeInTheDocument());
    expect(within(dialog).getByText(/still being produced/i)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Check status' })).toBeInTheDocument();
  });

  it('downloads a completed report from its row through the signed link', async () => {
    // The list carries no link (one per row on every page load would be minted
    // for rows nobody opens), so the row's Download reads the row's own record
    // and opens what the API signs for it. It used to only open the dialog.
    const user = userEvent.setup();
    const signed =
      '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3/download?expires=1760000000&sig=abc123';
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3'
        ? Promise.resolve({
            id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3',
            template: 'demand_summary',
            format: 'pdf',
            status: 'completed',
            download_url: signed,
            signed_url: signed,
            size_bytes: 2524,
            created_at: ISO,
          })
        : routeGet(path),
    );
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Download / }));

    await waitFor(() => expect(open).toHaveBeenCalledWith(signed, '_blank', 'noopener,noreferrer'));
  });

  it('polls an open report until the worker finishes it', async () => {
    // ``POST /reports/generate`` only queues the row; the worker renders it on
    // its next tick. Without a poll the dialog kept the status it opened with,
    // so a finished file only appeared after closing and reopening it.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let reads = 0;
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3') {
        reads += 1;
        return Promise.resolve(
          reads === 1
            ? { id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3', format: 'pdf', status: 'queued', size_bytes: null }
            : {
                id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3',
                template: 'demand_summary',
                format: 'pdf',
                status: 'completed',
                download_url: '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3/download?expires=1&sig=a',
                signed_url: '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3/download?expires=1&sig=a',
                size_bytes: 2524,
                created_at: ISO,
              },
        );
      }
      return routeGet(path);
    });
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Open report / }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByText('Waiting to start')).toBeInTheDocument());

    // Nothing has changed before the poll interval elapses…
    expect(within(dialog).getByRole('button', { name: 'Check status' })).toBeInTheDocument();

    // …and after it, the dialog is a finished report with a real file.
    await vi.advanceTimersByTimeAsync(3200);

    await waitFor(() => expect(within(dialog).getByText('Completed')).toBeInTheDocument());
    expect(within(dialog).getByRole('button', { name: 'Download' })).toBeInTheDocument();
    expect(within(dialog).getByText('2.5 KB')).toBeInTheDocument();
  });

  it('shows why a report failed instead of a bare Failed status', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/reports/b474cfb0-dce0-49bc-b78f-f6ccc8950ac3'
        ? Promise.resolve({
            id: 'b474cfb0-dce0-49bc-b78f-f6ccc8950ac3',
            template: 'demand_summary',
            format: 'pdf',
            status: 'failed',
            size_bytes: null,
            error: 'No demand recorded between 2026-08-20 and 2026-09-19',
          })
        : routeGet(path),
    );
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Open report / }));
    const dialog = await screen.findByRole('dialog');

    await waitFor(() => expect(within(dialog).getByText('Failed')).toBeInTheDocument());
    expect(within(dialog).getByText(/No demand recorded between/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Download' })).toBeNull();
  });

  it('lets a schedule be edited and deleted, which the UI never offered', async () => {
    const user = userEvent.setup();
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Edit schedule for / }));

    const dialog = await screen.findByRole('dialog', { name: 'Edit schedule' });
    // Prefilled from the schedule being edited, not blank.
    expect(within(dialog).getByLabelText('Cadence')).toHaveValue('weekly');
    expect(within(dialog).getByLabelText('Recipients')).toHaveValue('ops@example.com');

    await user.selectOptions(within(dialog).getByLabelText('Cadence'), 'monthly');
    await user.click(within(dialog).getByRole('button', { name: /save changes/i }));
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/reports/schedules/sch-1', {
        cadence: 'monthly',
        recipients: ['ops@example.com'],
      }),
    );

    await user.click(await screen.findByRole('button', { name: /^Edit schedule for / }));
    const again = await screen.findByRole('dialog', { name: 'Edit schedule' });
    await user.click(within(again).getByRole('button', { name: /^delete$/i }));
    await waitFor(() => expect((api.delete as unknown as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith('/api/v1/reports/schedules/sch-1'));
  });
});

/* -------------------------------------------------------------------- alerts */

describe('Alerts as a Data Clerk', () => {
  it('opens a detail view with the real alert, not just the summary line', async () => {
    const user = userEvent.setup();
    render(<Alerts onNavigate={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: /view details/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Nyarutarama coverage fell/)).toBeInTheDocument();
    expect(within(dialog).getByText('coverage_breach')).toBeInTheDocument();
    expect(within(dialog).getByText('Unread')).toBeInTheDocument();
  });

  it('shows the rule switches read-only and says why', async () => {
    render(<Alerts onNavigate={vi.fn()} />);
    const toggle = await screen.findByRole('switch', { name: 'coverage_breach' });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/can see which rules are active but not change them/i)).toBeInTheDocument();
  });

  it('lets an owner change the rules', async () => {
    session.user.permissions = ['alerts:read', 'alerts:configure'];
    render(<Alerts onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('switch', { name: 'coverage_breach' })).toBeEnabled());
  });
});

/* ------------------------------------------------------------------- quality */

describe('Quality Monitor as a Data Clerk', () => {
  it('names the location instead of printing a UUID prefix', async () => {
    render(<QualityMonitor />);

    // The name is deliberately shown in two places now — the issues table and
    // the location filter — so this asserts it is named, not that it appears
    // exactly once. What must never happen is the raw id reaching the screen.
    expect((await screen.findAllByText(/Kigali Heights Bistro/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/location loc-/i)).toBeNull();
  });

  it('shows the quality charts, not just tables', async () => {
    render(<QualityMonitor />);
    await screen.findByText('Completeness');

    // The four gallery panels. Their contents are recharts surfaces, which
    // measure themselves from a zero-size container under jsdom and draw
    // nothing, so the panel headings are what is assertable here.
    for (const heading of [
      'Issue severity mix',
      'Which checks are firing',
      'Issues by location',
      'How long issues have been open',
    ]) {
      expect(screen.getByText(heading)).toBeInTheDocument();
    }
  });

  it('opens an issue detail view with the fix, and keeps the fix buttons the clerk does hold', async () => {
    const user = userEvent.setup();
    render(<QualityMonitor />);
    await user.click(await screen.findByRole('button', { name: /view details/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Backfill from POS export')).toBeInTheDocument();
    expect(within(dialog).getByText('Kigali Heights Bistro')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /apply fix/i })).toBeEnabled();
  });

  it('shows validation rules read-only for the clerk', async () => {
    render(<QualityMonitor />);
    const toggle = await screen.findByRole('switch', { name: 'gap_detection' });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/can see which validation rules run but not change them/i)).toBeInTheDocument();
  });

  it('states each rule\u2019s dimension, cadence and what it has caught', async () => {
    render(<QualityMonitor />);

    // `gap_detection` is a known rule, so its card can say what it checks and
    // when it actually runs. The table this replaced printed "Every import" in
    // every row, including the connector check that runs hourly, because it
    // looked the rule up by `rule_key` — a field the API does not send.
    await waitFor(() => expect(screen.getByText('Detects gaps in a demand series')).toBeInTheDocument());
    expect(screen.getByText('Completeness check')).toBeInTheDocument();
    expect(screen.getByText('Every import')).toBeInTheDocument();
    expect(screen.getByText('1 of 1 running')).toBeInTheDocument();

    // The fixture's one open issue names no rule, so nothing can be attributed
    // to this check — and the card says so rather than implying it found it.
    expect(screen.getByText('Nothing open')).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------- sources */

describe('Sources as a Data Clerk', () => {
  it('opens a real detail view of the source instead of a decorative form', async () => {
    const user = userEvent.setup();
    render(<Sources onNavigate={vi.fn()} />);
    await user.click(await screen.findByLabelText('View details for Kigali POS'));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getAllByText('Kigali POS').length).toBeGreaterThan(0);
    expect(within(dialog).getByText('Last Sync At')).toBeInTheDocument();
    expect(within(dialog).getByText('1840')).toBeInTheDocument();
    // The old fake form is gone: no invented sync-cadence field.
    expect(within(dialog).queryByLabelText(/sync cadence/i)).toBeNull();
    expect(within(dialog).getByText(/connector configuration/i)).toBeInTheDocument();
  });

  it('renders a null field as an em dash instead of hiding it', async () => {
    const user = userEvent.setup();
    render(<Sources onNavigate={vi.fn()} />);
    await user.click(await screen.findByLabelText('View details for OCR Batch'));

    const dialog = await screen.findByRole('dialog');
    // "never synced" is exactly what a clerk needs to see, so the field must
    // survive the render filter even though its value is null.
    expect(within(dialog).getByText('Last Sync At')).toBeInTheDocument();
    expect(within(dialog).getByText('—')).toBeInTheDocument();
    expect(within(dialog).queryByText('src-2')).toBeNull();
  });
});

/* ------------------------------------------------------------------- profile */

describe('Profile as a Data Clerk', () => {
  it('does not ask for the admin-only feeds it cannot read', async () => {
    render(<Profile />);

    // The clerk's own locations and sessions are readable…
    await waitFor(() => expect(screen.getByText('Kigali Heights Bistro')).toBeInTheDocument());

    // …but the audit log needs audit:read, pipeline runs need pipeline:read and
    // uploads need ingest:write. Each used to be requested and its 403 swallowed,
    // which is what put "failed" requests in the console for a clerk.
    const paths = mockGet.mock.calls.map((call) => call[0]);
    expect(paths).not.toContain('/api/v1/audit-log');
    expect(paths).not.toContain('/api/v1/pipeline/runs');
    expect(paths).not.toContain('/api/v1/ingest/uploads');
  });

  it('names what changed in the activity feed, not the endpoint', async () => {
    session.user.permissions = ['workspace:read', 'audit:read'];
    render(<Profile />);

    // The action reads as the person's action rather than "Me preferences", and
    // the second line says what happened; the request path never reaches the feed.
    await waitFor(() => expect(screen.getByText('Preferences updated')).toBeInTheDocument());
    expect(screen.getByText(/accepted from the web app/)).toBeInTheDocument();
    expect(screen.queryByText(/\/api\/v1\/me\/preferences/)).toBeNull();
  });

  it("keeps a pipeline row's own summary when the audit log is out of reach", async () => {
    session.user.permissions = ['workspace:read', 'pipeline:read'];
    render(<Profile />);

    // The pipeline feed has no status metadata to report, so the row names the job
    // and keeps the run's own summary rather than inventing an outcome.
    await waitFor(() => expect(screen.getByText('Connector sync · Succeeded')).toBeInTheDocument());
    expect(screen.getByText(/synced 2 active connector\(s\)/)).toBeInTheDocument();
    expect(screen.queryByText(/accepted/)).toBeNull();
    expect(mockGet.mock.calls.map((call) => call[0])).not.toContain('/api/v1/audit-log');
  });

  it('labels the session it is using and offers the others for revoking', async () => {
    render(<Profile />);

    // `current` comes from the token's `sid` claim, so exactly one row is the
    // device you are on — it is labelled, and not offered up for revoking.
    await waitFor(() => expect(screen.getByText('This device')).toBeInTheDocument());
    expect(screen.getByText('iPhone')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Revoke' })).toHaveLength(1);
    // `trusted_device` is the login's "remember this device" flag.
    expect(screen.getByText('Trusted')).toBeInTheDocument();
  });

  it('requests the feeds a seat holding the capabilities may read', async () => {
    session.user.permissions = ['workspace:read', 'ingest:write', 'audit:read'];
    render(<Profile />);

    await waitFor(() =>
      expect(mockGet.mock.calls.map((call) => call[0])).toContain('/api/v1/audit-log'),
    );
    const paths = mockGet.mock.calls.map((call) => call[0]);
    expect(paths).toContain('/api/v1/ingest/uploads');
    // audit:read is held, so the run-list fallback is not reached.
    expect(paths).not.toContain('/api/v1/pipeline/runs');
  });
});

/* ------------------------------------------------------------------- billing */

describe('Billing as a Data Clerk', () => {
  it('keeps the plan readable and the owner-only controls disabled', async () => {
    render(<Billing />);

    // The clerk can see which plan the workspace is on and what it uses…
    await waitFor(() => expect(screen.getByText('Starter plan')).toBeInTheDocument());

    // …but switching it, changing the card, the contact or cancelling is the
    // owner's call: the API answers 403 "Owner role required", so those
    // controls are disabled rather than left to fail on click.
    expect(screen.getByRole('button', { name: /Switch to Growth/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Update payment method/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save contact/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Cancel plan/i })).toBeDisabled();
    expect(screen.getByText(/Only the owner can change the plan/i)).toBeInTheDocument();
  });

  it('lets an owner change the plan', async () => {
    session.user.permissions = ['workspace:read', 'billing:read', 'billing:manage'];
    render(<Billing />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Switch to Growth/i })).toBeEnabled());
    expect(screen.getByRole('button', { name: /Cancel plan/i })).toBeEnabled();
    expect(screen.queryByText(/Only the owner can change the plan/i)).not.toBeInTheDocument();
  });
});

/* ---------------------------------------------------------------- policy lab */

describe('Policy Lab as an Analyst', () => {
  it('simulates the policies but cannot save the workspace default', async () => {
    session.user.permissions = ['policies:read', 'policies:simulate'];
    render(<PolicyLab />);

    // Simulating is the analyst's own work…
    await waitFor(() => expect(screen.getByRole('button', { name: /Re-run simulation/i })).toBeEnabled());

    // …but PATCH /policies/{id} needs policies:write and answers 403 otherwise,
    // so the save control says why instead of failing on click.
    expect(screen.getByRole('button', { name: /Save as workspace default/i })).toBeDisabled();
    expect(screen.getByText(/does not set the workspace default/i)).toBeInTheDocument();
    expect(screen.queryByText(/Changes create a change_request/i)).not.toBeInTheDocument();
  });

  it('lets an administrator save the default', async () => {
    session.user.permissions = ['policies:read', 'policies:simulate', 'policies:write'];
    render(<PolicyLab />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Save as workspace default/i })).toBeEnabled());
    expect(screen.queryByText(/does not set the workspace default/i)).not.toBeInTheDocument();
  });

  it('hands a rule it could not compute the screen that fixes it', async () => {
    // A rule that averages observed demand has no basis when none is stored.
    // The panel explained that in prose and left the reader to find the import
    // screen themselves, even though it knows exactly what is missing.
    session.user.permissions = ['policies:read', 'policies:simulate'];
    const post = api.post as unknown as ReturnType<typeof vi.fn>;
    post.mockImplementation((path: string) =>
      path === '/api/v1/policies/simulate'
        ? Promise.resolve({
            location_id: 'loc-1',
            period_start: '2026-09-01',
            period_end: '2026-09-30',
            target_date: '2026-09-01',
            results: [
              {
                policy: 'uncertainty_aware',
                prepared_qty: null,
                surplus_sr: null,
                surplus_ps: null,
                unmet_ud: null,
                prepared_days: 0,
                realised_days: 0,
                surplus_days: 0,
                unmet_days: 0,
                quantile: 0.85,
                lookback_days: 28,
                unavailable_reason: 'no_history',
                unit: 'visitor',
              },
            ],
          })
        : Promise.resolve({}),
    );
    const user = userEvent.setup();
    render(<PolicyLab />);

    await user.click(await screen.findByRole('button', { name: /Import demand history/ }));
    expect(navigate).toHaveBeenCalledWith('ingestion');
  });

  it('says how many days the period figures were actually measured on', async () => {
    // A period figure is an average over the days the rule could be applied, so
    // "153 a day" over 29 of 29 days and over 3 of 29 days are different
    // claims. The endpoint reports both counts; nothing rendered them.
    session.user.permissions = ['policies:read', 'policies:simulate'];
    const post = api.post as unknown as ReturnType<typeof vi.fn>;
    post.mockImplementation((path: string) =>
      path === '/api/v1/policies/simulate'
        ? Promise.resolve({
            location_id: 'loc-1',
            period_start: '2026-09-01',
            period_end: '2026-09-30',
            target_date: '2026-09-01',
            results: [
              {
                policy: 'uncertainty_aware',
                prepared_qty: 153,
                prepared_total: 4590,
                surplus_sr: 0,
                surplus_ps: 120.5,
                unmet_ud: 8.2,
                prepared_days: 29,
                realised_days: 12,
                surplus_days: 7,
                unmet_days: 3,
                quantile: 0.85,
                lookback_days: 28,
                unit: 'visitor',
              },
            ],
          })
        : Promise.resolve({}),
    );
    render(<PolicyLab />);

    await waitFor(() =>
      expect(screen.getByText(/could be worked out for 29 of them/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/and 12 have an outcome recorded/)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ settings */

describe('Settings as a Data Clerk', () => {
  it('stops retrying the workspace profile after a failure, instead of flickering', async () => {
    // The load used to be guarded by its own `loading` flag: a failed fetch set
    // loading back to false, which changed the effect's dependencies, so it
    // fetched again — and failed again — forever. On any backend without
    // /workspace the Organization tab flickered continuously.
    const user = userEvent.setup();
    const boom = Object.assign(new Error('nope'), { status: 404 });
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/workspace' ? Promise.reject(boom) : routeGet(path),
    );

    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Organization' }));

    await waitFor(() => expect(screen.getByText('Could not load the workspace')).toBeInTheDocument());
    // Give the loop time to spin, then prove it did not.
    await new Promise((r) => setTimeout(r, 250));
    const calls = mockGet.mock.calls.filter(([path]) => path === '/api/v1/workspace');
    expect(calls).toHaveLength(1);

    // Retry is the deliberate way to ask again, and asks exactly once more.
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByText('Could not load the workspace')).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 250));
    expect(mockGet.mock.calls.filter(([path]) => path === '/api/v1/workspace')).toHaveLength(2);
  });

  it('shows the workspace profile read-only, with real footprint numbers', async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Organization' }));

    await waitFor(() => expect(screen.getByLabelText('Workspace name')).toBeInTheDocument());
    expect(screen.getByLabelText('Workspace name')).toHaveValue('DemandIQ Demo');
    expect(screen.getByLabelText('Workspace name')).toHaveAttribute('readonly');
    expect(screen.getByText('6')).toBeInTheDocument(); // members
    expect(screen.getByText('14')).toBeInTheDocument(); // forecasts
    expect(screen.getByText(/can read the workspace but not change it/i)).toBeInTheDocument();
    // No "Coming soon" stub left behind.
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  it('lets an owner edit the workspace profile, and PATCHes the real endpoint', async () => {
    const user = userEvent.setup();
    session.user.permissions = ['workspace:read', 'workspace:manage'];
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/workspace'
        ? Promise.resolve({
            id: 'ws-1',
            name: 'DemandIQ Demo',
            business_type: 'cafe',
            country: 'RW',
            timezone: 'Africa/Kigali',
            created_at: ISO,
            counts: { users: 6, locations: 2, forecasts: 14 },
            can_manage: true,
          })
        : routeGet(path),
    );
    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Organization' }));

    const name = await screen.findByLabelText('Workspace name');
    await user.clear(name);
    await user.type(name, 'Demo HQ');
    await user.click(screen.getByRole('button', { name: /save workspace/i }));

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/workspace', expect.objectContaining({ name: 'Demo HQ' })),
    );
  });

  it('shows when a sign-in method was linked, not only that it is', async () => {
    // `/me/identities` sends `linked_at` beside the provider; the panel only
    // ever said whether Google was connected.
    const user = userEvent.setup();
    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Security' }));

    await waitFor(() => expect(screen.getByText(/^Linked /)).toBeInTheDocument());
    expect(screen.getByText(/Sign in with Google is enabled/)).toBeInTheDocument();
  });

  it('says nothing about when a method was linked if the API does not report it', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/me/identities'
        ? Promise.resolve([{ provider: 'google' }])
        : routeGet(path),
    );
    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Security' }));

    await waitFor(() => expect(screen.getByText(/Sign in with Google is enabled/)).toBeInTheDocument());
    expect(screen.queryByText(/^Linked /)).toBeNull();
  });

  it('sets the location the app opens on, and PATCHes the stored preference', async () => {
    // `GET/PATCH /me/preferences` has always carried `default_location`; no
    // control ever set it, so the preference could only be changed by a client
    // outside the app.
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/me/preferences')
        return Promise.resolve({
          theme: 'light',
          language: 'en',
          density: 'comfortable',
          default_location: 'loc-1',
        });
      if (path === '/api/v1/locations')
        return Promise.resolve({
          data: [
            { id: 'loc-1', name: 'Kigali Heights Bistro' },
            { id: 'loc-2', name: 'Kimironko Kiosk' },
          ],
        });
      return routeGet(path);
    });
    render(<Settings />);
    await user.click(await screen.findByRole('button', { name: 'Preferences' }));

    const select = await screen.findByLabelText('Default location');
    // The control opens on the stored choice, not on a default of its own.
    await waitFor(() => expect(select).toHaveValue('loc-1'));
    expect(within(select).getByRole('option', { name: 'Kimironko Kiosk' })).toBeInTheDocument();

    await user.selectOptions(select, 'loc-2');
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/me/preferences', { default_location: 'loc-2' }),
    );

    // "Ask each time" is a real choice, and the API stores it as null.
    await user.selectOptions(select, '');
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/me/preferences', { default_location: null }),
    );
  });

  it('reports two-factor from the account, and starts from the saved phone', async () => {
    const user = userEvent.setup();
    render(<Settings />);
    // Profile starts from the account's own phone number, not a placeholder.
    await waitFor(() => expect(screen.getByLabelText('Phone')).toHaveValue('+250 788 111 222'));

    await user.click(screen.getByRole('button', { name: 'Security' }));
    // mfa_enabled is false on this seat, so the pill must not claim otherwise.
    await waitFor(() => expect(screen.getByText('Off')).toBeInTheDocument());
  });
});

/* ------------------------------------------------------- plain language gate */

/* ------------------------------------------------- validation rules as cards */

describe('Validation rules present as cards, not as a configuration grid', () => {
  it('keeps the machine token on the switch while the copy stays human', async () => {
    render(<QualityMonitor />);

    // `gap_detection` is the switch's accessible name — the contract the rest of
    // this suite and RULE_META key off — but it must not be *visible* anywhere.
    await waitFor(() => expect(screen.getByRole('switch', { name: 'gap_detection' })).toBeInTheDocument());
    expect(screen.getByText('Gap detection')).toBeInTheDocument();
    expect(screen.queryByText(/gap_detection/i)).toBeNull();
  });
});

describe('Operator screens show a skeleton on first load', () => {
  it('keeps the page shape instead of a bare spinner', async () => {
    // A read that never resolves leaves the screen in its first-load state.
    mockGet.mockImplementation(() => new Promise(() => undefined));
    render(<QualityMonitor />);

    // The accessible name matches the one the old spinner announced.
    await waitFor(() => expect(screen.getByRole('status', { name: 'Loading quality data' })).toBeInTheDocument());
  });
});

describe('Operator screens speak plain language', () => {
  // A machine token that reaches the screen is a bug: an operator cannot act on
  // `timeliness`, `success` or `{"coverage_lt":0.85}`. These assert both halves —
  // the plain words are present, and the raw token is absent.

  it('Quality Monitor names each check instead of printing its key', async () => {
    render(<QualityMonitor />);

    await waitFor(() => expect(screen.getByText('Completeness')).toBeInTheDocument());
    // `timeliness` is mapped to the human word "Freshness".
    expect(screen.getByText('Freshness')).toBeInTheDocument();
    expect(screen.getByText(/how recently new data arrived/i)).toBeInTheDocument();

    expect(screen.queryByText('timeliness')).toBeNull();
    expect(screen.queryByText(/timeliness dimension/i)).toBeNull();
    expect(screen.queryByText(/weighted across 4 dimensions/i)).toBeNull();
  });

  it('Alerts explains its triggers without acronyms or raw JSON', async () => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/alerts/rules'
        ? Promise.resolve({
            data: [
              {
                id: 'rule-1',
                name: 'coverage_breach',
                description: 'Alerts when coverage drops',
                enabled: true,
                threshold: { coverage_lt: 0.85 },
              },
            ],
          })
        : routeGet(path),
    );
    render(<Alerts onNavigate={vi.fn()} />);

    // The "how it works" list is in operator words.
    await waitFor(() => expect(screen.getByText(/Big forecast miss/i)).toBeInTheDocument());
    expect(screen.getByText(/Range not holding/i)).toBeInTheDocument();
    expect(screen.getByText(/Demand pattern change/i)).toBeInTheDocument();
    expect(screen.getByText(/Quiet source/i)).toBeInTheDocument();

    // The rule row describes the threshold, rather than dumping it as JSON.
    expect(screen.getByText(/Fires when the forecast range holds less often than 0.85/i)).toBeInTheDocument();
    expect(screen.queryByText(/\{"coverage_lt"/)).toBeNull();

    // No bare modelling acronyms on the reading seat's screen.
    expect(screen.queryByText(/\bPSI\b/)).toBeNull();
    expect(screen.queryByText(/\bMAE\b/)).toBeNull();
  });

  it('Sources translates a run status and a channel type', async () => {
    render(<Sources onNavigate={vi.fn()} />);

    // `status: 'success'` in the sync history reads as "Succeeded".
    await waitFor(() => expect(screen.getByText('Succeeded')).toBeInTheDocument());
    expect(screen.queryByText('success')).toBeNull();

    // `type: 'ocr'` reads as "Image OCR", not the raw token. The channel filter
    // now offers the same humanised name as a dropdown option, so the row is
    // matched among the matches rather than asserted unique.
    expect(screen.getAllByText(/^Image OCR/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/^pos ·/)).toBeNull();
  });

  it('Reports shows a report status in words', async () => {
    const user = userEvent.setup();
    render(<Reports />);

    await user.click(await screen.findByRole('button', { name: /^Open report / }));
    const dialog = await screen.findByRole('dialog');

    await waitFor(() => expect(within(dialog).getByText('Completed')).toBeInTheDocument());
    expect(within(dialog).queryByText('completed')).toBeNull();
  });
});
