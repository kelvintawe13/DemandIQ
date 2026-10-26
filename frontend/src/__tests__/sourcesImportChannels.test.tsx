import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// The Sources page is a view over connectors, and ingestion now registers a
// channel per import kind (spreadsheet, photo, hand entry, till push). Those
// channels receive data and cannot be pulled, so the screen has to say that on
// the control rather than offering a sync the API answers 409 for.

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      name: 'Ada Admin',
      role: 'Administrator',
      roleKey: 'admin',
      email: 'admin@demandiq.app',
      permissions: ['sources:read', 'pipeline:read'],
    },
  }),
}));

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

/** One hand-entry channel, as `/sources` reports the row ingestion created. */
const MANUAL_CHANNEL = {
  id: 'src-manual',
  name: 'Manual entry',
  type: 'manual',
  status: 'available',
  freshness: 'fresh',
  last_sync_at: '2026-09-22T04:00:00Z',
  volume: 35,
  health: 'healthy',
  import_kind: 'manual',
  last_pull: {
    pulled: 35,
    committed: 35,
    skipped: 0,
    detail: '35 new, 0 corrected from 35 hand-entered rows',
  },
  created_at: '2026-01-01T00:00:00Z',
};

function wireApi() {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/sources') return Promise.resolve({ data: [MANUAL_CHANNEL] });
    if (path === '/api/v1/sources/sync-history') return Promise.resolve({ data: [] });
    if (path === '/api/v1/pipeline/schedule') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
}

describe('Sources: import channels', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wireApi();
  });

  it('lists the channel the import wrote to, with its real volume', async () => {
    render(<Sources onNavigate={() => {}} />);

    // Named as a channel row, not just as a filter option: the page also offers
    // every channel kind in its "Channel" dropdown.
    expect(
      await screen.findByRole('button', { name: 'View details for Manual entry' }),
    ).toBeInTheDocument();
    // The channel's own last pull, not a job row: 35 rows are attributed to it.
    expect(screen.getByText('35 committed')).toBeInTheDocument();
  });

  it('does not offer a pull for a channel that receives imports', async () => {
    render(<Sources onNavigate={() => {}} />);

    const sync = await screen.findByRole('button', { name: 'Sync Manual entry' });
    expect(sync).toBeDisabled();
    // The reason names the channel rather than telling the operator to activate
    // a connector: activating it would not give an upload channel an endpoint.
    expect(sync).toHaveAttribute('title', 'Filled by manual entry — there is nothing to pull');
  });
});
