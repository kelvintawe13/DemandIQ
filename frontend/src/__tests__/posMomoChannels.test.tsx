/**
 * The POS / Mobile Money tab must report what the API says, and nothing else.
 *
 * It used to be a prototype with no API call at all: "Authorise" set a local
 * flag and announced a 90-day backfill, "Import 4 days" announced an import,
 * and the preview listed four hardcoded dates (2026-09-15 … 2026-09-18). The
 * screen could therefore report demand as ingested when nothing had been
 * written — the same failure the "2 rows extracted (demo)" copy had on the
 * image tab.
 *
 * These tests pin the tab to `GET /sources`, `POST /sources/{id}/sync` and
 * `GET /sources/sync-history`, and pin the two things it must not do: show
 * invented transactions, and report a queued run as a finished import.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

const session = { user: { permissions: ['sources:read', 'sources:write'] } };
vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));
vi.mock('../contexts/NavigationContext', () => ({ useNavigation: () => ({ navigate: vi.fn() }) }));
const toast = vi.fn();
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { PosMomoTab } from '../components/ingestion/PosMomoTab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const POS_SOURCE = {
  id: 'pos-1',
  name: 'Kimironko Till 1',
  type: 'pos',
  // `active` is the one status `_job_connector_sync` acts on; the tab's Sync is
  // tied to exactly that set, so the fixture that exercises Sync must be active.
  status: 'active',
  freshness: 'never',
  last_sync_at: null,
  volume: 0,
  health: 'unknown',
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  toast.mockReset();
  session.user.permissions = ['sources:read', 'sources:write'];
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/sources') return Promise.resolve({ data: [POS_SOURCE] });
    if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
});

describe('POS / MoMo tab', () => {
  it('names the channels the API reports', async () => {
    render(<PosMomoTab />);
    await waitFor(() => expect(screen.getByText('Kimironko Till 1')).toBeInTheDocument());
    expect(screen.getByText(/POS · never · 0 rows ingested/)).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    // No `last_pull` in the payload means no pull has run — said, not faked.
    expect(screen.getByText('No pull yet')).toBeInTheDocument();
  });

  it('shows what the channel\u2019s own last pull did', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/sources')
        return Promise.resolve({
          data: [
            {
              ...POS_SOURCE,
              freshness: 'fresh',
              last_pull: {
                pulled: 9,
                committed: 8,
                skipped: 1,
                detail: 'pulled 9 row(s): committed 8, skipped 1',
              },
            },
          ],
        });
      if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    render(<PosMomoTab />);

    // The per-channel outcome, not the workspace-wide run list.
    await waitFor(() =>
      expect(screen.getByText('Last pull: 8 rows committed, 1 skipped')).toBeInTheDocument(),
    );
  });

  it('refuses to sync a channel the job would skip, and says why', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/sources')
        // `available` is what creating a connector defaults to, and the job
        // selects on `status == "active"` — so a sync here pulls nothing.
        return Promise.resolve({ data: [{ ...POS_SOURCE, status: 'available' }] });
      if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    render(<PosMomoTab />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sync now/ })).toBeDisabled();
      expect(
        screen.getByText(/is available, not active. A sync only pulls active channels/),
      ).toBeInTheDocument();
      expect(screen.getByText(/No channel is active, so a sync would pull nothing/)).toBeInTheDocument();
    });

    // Pressing the row's own Sync must not queue a run that would settle as a
    // success having done nothing.
    fireEvent.click(screen.getByRole('button', { name: 'Sync' }));
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('never shows the hardcoded preview rows it used to ship', async () => {
    render(<PosMomoTab />);
    await screen.findByText('Kimironko Till 1');

    // These four dates were literal constants in the old component. Nothing in
    // this workspace's data contains them, so seeing one means the mock is back.
    for (const invented of ['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']) {
      expect(screen.queryByText(new RegExp(invented))).toBeNull();
    }
    expect(screen.queryByText(/covers/)).toBeNull();
  });

  it('queues a real sync and reports it as queued, not imported', async () => {
    mockPost.mockResolvedValue({ job_id: 'abcdef12-3456-7890-abcd-ef1234567890', status: 'queued' });
    render(<PosMomoTab />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Sync now/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /Sync now/ }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/sources/pos-1/sync'),
    );
    // The API queues the run; it does not perform it. "imported" here is exactly
    // the claim the old tab made for a request it never sent.
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('sync queued')));
    const said = toast.mock.calls.map((c) => String(c[0])).join(' ');
    expect(said).not.toMatch(/imported/i);
  });

  it('says so when no channel is connected instead of offering a dead button', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/sources') return Promise.resolve({ data: [] });
      if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    render(<PosMomoTab />);

    await waitFor(() => expect(screen.getByText('No POS or mobile-money channel')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Sync now/ })).toBeDisabled();
    expect(screen.getByText(/No POS channel is connected in this workspace yet/)).toBeInTheDocument();
  });

  it('does not claim a channel of another kind', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/sources')
        return Promise.resolve({
          data: [{ ...POS_SOURCE, id: 'ocr-1', name: 'Santa', type: 'ocr' }],
        });
      if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    render(<PosMomoTab />);

    // An OCR connector is real, and it is not a POS channel: it belongs on
    // Sources, not in this tab's connection count.
    await screen.findByText('No POS or mobile-money channel');
    expect(screen.queryByText('Santa')).toBeNull();
  });

  it('offers to add a channel only to a seat that may', async () => {
    session.user.permissions = ['sources:read', 'sources:write', 'connectors:manage'];
    const { unmount } = render(<PosMomoTab />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Add or configure a channel/ })).toBeInTheDocument(),
    );
    unmount();

    // Without `connectors:manage` the API answers 403 (verified), so the tab
    // explains who can instead of routing to a page that would refuse.
    session.user.permissions = ['sources:read', 'sources:write'];
    render(<PosMomoTab />);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Add or configure a channel/ })).toBeNull();
      expect(screen.getByRole('note')).toHaveTextContent(/Connectors page/);
    });
  });

  it('shows the real sync history rather than an invented one', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/sources') return Promise.resolve({ data: [POS_SOURCE] });
      if (path === '/api/v1/sources/sync-history')
        return Promise.resolve({
          data: [
            {
              id: 'run-1',
              job_type: 'connector_sync',
              status: 'queued',
              rows_processed: null,
              duration_ms: null,
              started_at: '2026-09-30T13:00:00Z',
            },
            {
              id: 'run-2',
              job_type: 'feature_materialisation',
              status: 'success',
              rows_processed: 4,
              duration_ms: 2100,
              started_at: '2026-09-30T12:00:00Z',
            },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<PosMomoTab />);

    // The pull is the run that matters here; a feature build is a different job.
    await waitFor(() => {
      expect(screen.getByText('Connector sync')).toBeInTheDocument();
      expect(screen.queryByText('Feature materialisation')).toBeNull();
      // An unrun job reports no rows rather than a zero it cannot support.
      expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    });
  });
});
