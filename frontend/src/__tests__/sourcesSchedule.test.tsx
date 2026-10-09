import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// The Sources page now shows the ingestion cadence (from GET /pipeline/schedule)
// and labels sync-history rows by job. Two things used to be wrong here: the
// history's first column said "Source" while rendering a job_type, and the
// detail modal filtered runs by `run.source_id` — a field the endpoint does not
// return, so "Recent runs for this source" was always empty.

// Hoisted so the mock factory can close over it: factories run while the module
// graph is evaluated, before a plain `const` below would have been initialised.
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

// A mutable seat: the page decides whether to ask for the schedule from the
// capabilities it holds, so a case can hand it a clerk's set instead.
const session = vi.hoisted(() => ({
  user: {
    name: 'Ada Admin',
    role: 'Administrator',
    roleKey: 'admin',
    email: 'admin@demandiq.app',
    permissions: ['sources:read', 'pipeline:read'] as string[],
  },
}));
vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 403;
    error = { message: 'Insufficient permissions for this action' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { Sources } from '../pages/Sources';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const MINUTE = 60_000;

function schedulePayload() {
  return {
    data: [
      {
        job_type: 'connector_sync',
        cadence: 'Every 15 min',
        cron: '*/15 * * * *',
        last_run: { status: 'success', rows_processed: 12, started_at: '2026-09-22T04:00:00Z', detail: 'synced 2' },
        next_run: new Date(Date.now() + 15 * MINUTE).toISOString(),
      },
      {
        job_type: 'feature_materialisation',
        cadence: '03:20 daily',
        cron: '20 3 * * *',
        last_run: null,
        next_run: new Date(Date.now() + 20 * 60 * MINUTE).toISOString(),
      },
      {
        job_type: 'full_retrain',
        cadence: 'Sundays 02:00',
        cron: '0 2 * * 0',
        last_run: { status: 'failed', started_at: '2026-09-21T02:00:00Z' },
        next_run: new Date(Date.now() + 5 * 24 * 60 * MINUTE).toISOString(),
      },
    ],
  };
}

const CONNECTOR = {
  id: 'src-1',
  name: 'MoMo till',
  type: 'momo',
  status: 'active',
  freshness: 'fresh',
  last_sync_at: '2026-09-22T04:00:00Z',
  volume: 640,
  health: 'healthy',
  last_pull: { pulled: 14, committed: 12, skipped: 2, detail: 'pulled 14 row(s): committed 12, skipped 2' },
  created_at: '2026-01-01T00:00:00Z',
};

const RUNS = {
  data: [
    {
      id: 'run-1',
      job_type: 'connector_sync',
      status: 'success',
      rows_processed: 12,
      duration_ms: 1500,
      detail: 'synced 2 active connector(s)',
      started_at: '2026-09-22T04:00:00Z',
    },
    {
      id: 'run-2',
      job_type: 'connector_sync',
      status: 'queued',
      rows_processed: null,
      duration_ms: null,
      detail: 'manual sync for connector src-1',
      started_at: '2026-09-22T04:05:00Z',
    },
  ],
};

/** Wire the API; `schedule` may be a rejection to model a caller without read rights. */
function wireApi(schedule: unknown | Error = schedulePayload(), connector = CONNECTOR) {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/sources') return Promise.resolve({ data: [connector] });
    if (path === '/api/v1/sources/sync-history') return Promise.resolve(RUNS);
    if (path === '/api/v1/pipeline/schedule') {
      return schedule instanceof Error ? Promise.reject(schedule) : Promise.resolve(schedule);
    }
    return Promise.resolve({ data: [] });
  });
}

async function renderSources() {
  render(<Sources onNavigate={() => {}} />);
  await screen.findByRole('heading', { name: 'Connected sources' });
}

// Administrator by default; a case that cares about capabilities sets its own.
beforeEach(() => {
  session.user.permissions = ['sources:read', 'pipeline:read'];
});

const requestedPaths = () => mockGet.mock.calls.map((call) => call[0]);

describe('Sources: ingestion cadence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the ingestion jobs with their cadence, last run and next run', async () => {
    wireApi();
    await renderSources();

    // The panel is titled, not just rendered: it exists.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Ingestion cadence' })).toBeInTheDocument());

    // "Connector sync" also appears in the history table; the cadence strings
    // below exist only in this panel, so they are what proves it rendered.
    expect(screen.getAllByText('Connector sync').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Every 15 min')).toBeInTheDocument();
    expect(screen.getByText('Feature materialisation')).toBeInTheDocument();

    // A job that has never run says so; it is not given an invented timestamp.
    expect(screen.getByText('Never')).toBeInTheDocument();

    // Next run is rendered relative to now, from the server's timestamp.
    const nextLabels = screen.getAllByText(/^in \d+[mh]$/).map((el) => el.textContent);
    expect(nextLabels).toContain('in 15m');
  });

  it('leaves non-ingestion jobs to the pipeline page', async () => {
    wireApi();
    await renderSources();

    await screen.findByRole('heading', { name: 'Ingestion cadence' });
    // Full retrain is a modelling job and is listed on Pipeline Health instead.
    expect(screen.queryByText('Full retrain')).not.toBeInTheDocument();
    expect(screen.queryByText('Sundays 02:00')).not.toBeInTheDocument();
  });

  it('never asks for the schedule when the seat cannot read pipeline info', async () => {
    // A data clerk has SOURCES_READ but not PIPELINE_READ. The page must hide the
    // cadence panel *without* sending the request — firing it and swallowing the
    // 403 left a rejected call in the console for a read the page knew it could
    // not make.
    session.user.permissions = ['sources:read'];
    wireApi();
    await renderSources();

    expect(screen.getByText('MoMo till')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ingestion cadence' })).not.toBeInTheDocument();
    expect(requestedPaths()).not.toContain('/api/v1/pipeline/schedule');
  });

  it('hides the panel rather than guessing when the schedule read fails', async () => {
    // Same outcome for a seat that may read it: if the endpoint fails, no cadence
    // is invented on screen.
    wireApi(Object.assign(new Error('forbidden'), { status: 403 }));
    await renderSources();

    expect(screen.getByText('MoMo till')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ingestion cadence' })).not.toBeInTheDocument();
    expect(screen.queryByText('Every 15 min')).not.toBeInTheDocument();
  });
});

describe('Sources: sync history', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wireApi();
  });

  it('labels rows by job and shows the run detail', async () => {
    await renderSources();

    expect(screen.getByRole('columnheader', { name: 'Job' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Source' })).not.toBeInTheDocument();

    expect(screen.getAllByText('Connector sync').length).toBeGreaterThan(0);
    expect(screen.getByText('synced 2 active connector(s)')).toBeInTheDocument();
    expect(screen.getByText('manual sync for connector src-1')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Duration' })).toBeInTheDocument();
  });

  it('queues a sync and reports the job id', async () => {
    mockPost.mockResolvedValue({ job_id: 'job-9', status: 'queued', source_id: 'src-1' });
    await renderSources();

    await userEvent.click(screen.getByRole('button', { name: 'Sync MoMo till' }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/sources/src-1/sync'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('job-9')));
  });

  it('shows the channel\u2019s own last pull outcome, not only the job-level rows', async () => {
    await renderSources();

    expect(screen.getByRole('columnheader', { name: 'Last pull' })).toBeInTheDocument();
    // The per-channel outcome the API reports on `GET /sources`; the run table
    // cannot carry it because `pipeline_runs` records no connector id.
    expect(screen.getByText('12 committed · 2 skipped')).toBeInTheDocument();
  });

  it('disables Sync for a channel the API would refuse, instead of sending a doomed request', async () => {
    // `available` is what creating a connector defaults to; POST /sources/{id}/sync
    // answers 409 not_active for it and the job never selects it. The button is
    // disabled with the reason rather than letting the operator discover it.
    wireApi(schedulePayload(), { ...CONNECTOR, status: 'available', freshness: 'never' });
    await renderSources();

    const sync = screen.getByRole('button', { name: 'Sync MoMo till' });
    expect(sync).toBeDisabled();
    expect(sync).toHaveAttribute('title', expect.stringContaining('Only an active channel'));
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('lists workspace-wide runs in the detail modal instead of an always-empty per-source filter', async () => {
    await renderSources();

    await userEvent.click(screen.getByRole('button', { name: 'View details for MoMo till' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('Recent sync runs (workspace-wide)')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Connector sync')).toHaveLength(2);
    // The section says where a *per-source* fact does come from, instead of
    // filtering by a field the API never returns.
    expect(
      within(dialog).getByText(/freshness comes from its last sync above/),
    ).toBeInTheDocument();
    // The source's own freshness is the per-source fact that *is* recorded.
    expect(within(dialog).getByText(/Last Sync At/i)).toBeInTheDocument();
  });
});
