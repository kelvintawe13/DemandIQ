import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// The console is fleet administration: a seat that can enrol a device, rotate a
// key and grant a capability, which is the shape most of these assertions need.
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      id: 'u1',
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      permissions: ['devices:read', 'devices:manage', 'device_grants:manage'],
    },
  }),
}));
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    constructor(
      public status: number,
      public error: { code: string; message: string; reference?: string | null } = {
        code: 'server_error',
        message: 'Request failed',
      },
    ) {
      super(error.message);
    }
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api, ApiException } from '../api/client';
import { DeviceFleet } from '../pages/DeviceFleet';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockDelete = api.delete as unknown as ReturnType<typeof vi.fn>;

const DEVICES = {
  data: [
    {
      id: 'd1',
      label: 'Till 2',
      device_type: 'pos',
      platform: 'Android 13',
      app_version: '2.4.0',
      location_id: 'loc1',
      credential_prefix: 'dq_a1b2',
      scopes: ['sales:write', 'stock:read'],
      status: 'active',
      last_seen_at: '2026-09-25T09:55:00Z',
      enrolled_at: '2026-08-01T10:00:00Z',
      enrolled_by: 'u1',
      revoked_at: null,
      mirroring_consent_at: '2026-09-10T08:00:00Z',
    },
    {
      id: 'd2',
      label: 'KDS pass',
      device_type: 'kds',
      platform: 'Android 12',
      app_version: '2.3.9',
      location_id: 'loc2',
      credential_prefix: 'dq_c3d4',
      scopes: ['orders:read'],
      status: 'quarantined',
      last_seen_at: null,
      enrolled_at: '2026-07-14T12:00:00Z',
      enrolled_by: 'u1',
      revoked_at: null,
      mirroring_consent_at: null,
    },
  ],
};

const LOCATIONS = {
  data: [
    { id: 'loc1', name: 'Kigali, Nyamirambo' },
    { id: 'loc2', name: 'Kigali, Remera' },
  ],
};

const SCOPES = {
  data: {
    base_scopes: ['sales:write', 'stock:read'],
    granted: [
      {
        capability: 'refunds:approve',
        grant_id: 'g1',
        granted_by: 'u1',
        granted_at: '2026-09-05T09:00:00Z',
        expires_at: null,
        reason: 'Kigali manager is on shift',
      },
    ],
    effective: ['sales:write', 'stock:read', 'refunds:approve'],
    withheld: [{ capability: 'menu:write', detail: 'Never granted to this device.' }],
  },
};

const GRANTS = {
  data: [
    {
      id: 'g1',
      capability: 'refunds:approve',
      granted_by: 'u1',
      granted_at: '2026-09-05T09:00:00Z',
      expires_at: null,
      revoked_at: null,
      reason: 'Kigali manager is on shift',
      state: 'active',
      effective: true,
      detail: 'Granted and in effect.',
    },
  ],
};

const SECRET = {
  data: {
    label: 'Till 2',
    secret: 'dq_live_7f3c9a2b1d4e',
    credential_prefix: 'dq_7f3c',
    warning: 'Store this secret securely. It will not be shown again.',
  },
};

const EVENTS = {
  data: [
    {
      id: 'e1',
      device_id: 'd1',
      event_type: 'heartbeat',
      payload: { app_version: '2.4.0', battery: 82 },
      recorded_at: '2026-09-25T09:55:00Z',
    },
  ],
};

function routeGet(path: string) {
  if (path === '/api/v1/devices') return Promise.resolve(DEVICES);
  if (path === '/api/v1/locations') return Promise.resolve(LOCATIONS);
  if (path.includes('/effective-scopes')) return Promise.resolve(SCOPES);
  if (path.includes('/grants')) return Promise.resolve(GRANTS);
  if (path.includes('/events')) return Promise.resolve(EVENTS);
  return Promise.resolve({});
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
  toast.mockReset();
  mockGet.mockImplementation(routeGet);
  mockPost.mockResolvedValue(SECRET);
  mockDelete.mockResolvedValue({});
  Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  // See `devices.test.tsx`: `vi.restoreAllMocks` leaves `vi.fn()` mocks holding
  // their implementations, and Vitest calls a reset mock with no arguments
  // during restore — which fails the test if that mock rejects.
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
  toast.mockReset();
});

/** The table row for one device, so an assertion cannot drift onto a filter. */
function rowFor(label: string): HTMLElement {
  const row = screen.getAllByRole('row').find((candidate) => candidate.textContent?.includes(label));
  if (!row) throw new Error(`no table row for ${label}`);
  return row;
}

describe('Device fleet console', () => {
  it('lists the restaurant’s own hardware, and asks the server for it', async () => {
    render(<DeviceFleet />);

    const till = await screen.findByRole('button', { name: 'Manage Till 2' });
    expect(till).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Manage KDS pass' })).toBeInTheDocument();

    // Type, restaurant, status, last seen and app version — everything the
    // operator is asked to recognise a till by.
    const row = rowFor('Till 2');
    expect(within(row).getByText('Till')).toBeInTheDocument();
    expect(within(row).getByText('Kigali, Nyamirambo')).toBeInTheDocument();
    expect(within(row).getByText('In service')).toBeInTheDocument();
    expect(within(row).getByText('2.4.0')).toBeInTheDocument();
    expect(within(rowFor('KDS pass')).getByText('Quarantined')).toBeInTheDocument();
    expect(within(rowFor('KDS pass')).getByText('Never seen')).toBeInTheDocument();
    expect(screen.getByText(/2 devices enrolled in this workspace/)).toBeInTheDocument();

    // The filters belong to the API: the fleet endpoint is the one place the
    // answer is built, and a client-side copy would be a second answer to
    // "which devices can this seat see".
    expect(mockGet).toHaveBeenCalledWith('/api/v1/devices', {
      status: undefined,
      location_id: undefined,
    });
  });

  it('sends a filter to the API rather than hiding rows on the client', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') return Promise.resolve(DEVICES);
      return routeGet(path);
    });
    render(<DeviceFleet />);
    // Scoped to the table row: a device's name now appears twice on the page —
    // once in the list and once in the attention panel that references it.
    await screen.findByRole('button', { name: 'Manage Till 2' });

    await user.selectOptions(await screen.findByLabelText('Filter by status'), 'quarantined');

    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/devices', {
        status: 'quarantined',
        location_id: undefined,
      }),
    );
  });

  it('says a filter matched nothing, and offers the way back', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') return Promise.resolve({ data: [] });
      return routeGet(path);
    });
    render(<DeviceFleet />);
    await user.selectOptions(await screen.findByLabelText('Filter by restaurant'), 'loc1');

    await waitFor(() => expect(screen.getByText('No devices match this filter')).toBeInTheDocument());
    expect(screen.getByText(/Nothing in this workspace has that status/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show every device' }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/devices', {
        status: undefined,
        location_id: undefined,
      }),
    );
  });

  it('distinguishes an empty fleet from a filtered one, and explains what enrolling does', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') return Promise.resolve({ data: [] });
      return routeGet(path);
    });
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByText('No devices enrolled yet')).toBeInTheDocument());
    expect(screen.getByText(/It is shown once and never again/)).toBeInTheDocument();
    expect(screen.queryByText('No devices match this filter')).not.toBeInTheDocument();
  });

  it('shows a device’s key exactly once, and says so while it is on screen', async () => {
    const user = userEvent.setup();
    render(<DeviceFleet />);
    await user.click(await screen.findByRole('button', { name: 'Enrol a device' }));

    await user.type(screen.getByLabelText('What the staff will know it as'), 'Till 3');
    await user.click(screen.getByRole('button', { name: /Enrol and show the key/ }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Copy this now. It is not shown again.')).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue('dq_live_7f3c9a2b1d4e')).toBeInTheDocument();
    expect(
      within(dialog).getByText(/It is shown once, at the moment it is created/),
    ).toBeInTheDocument();
    expect(mockPost).toHaveBeenCalledWith('/api/v1/devices', {
      label: 'Till 3',
      device_type: 'pos',
    });

    // Closing it is final: there is no second read of a secret, so the page
    // re-reads the fleet rather than asking for the key again.
    await user.click(within(dialog).getByRole('button', { name: 'I have written it down' }));
    await waitFor(() =>
      expect(screen.queryByDisplayValue('dq_live_7f3c9a2b1d4e')).not.toBeInTheDocument(),
    );
  });

  it('explains what a device can do and what it cannot, with the reason', async () => {
    const user = userEvent.setup();
    render(<DeviceFleet />);
    await user.click(await screen.findByRole('button', { name: 'Manage Till 2' }));

    // Effective scopes, read from the server on every load.
    await waitFor(() => expect(screen.getByText('What this device can do')).toBeInTheDocument());
    expect(screen.getByText('sales:write')).toBeInTheDocument();
    // The granted capability appears in both the effective list and the grant
    // history; what matters is that each place attributes it correctly.
    expect(screen.getAllByText('refunds:approve')).toHaveLength(2);
    // Two base scopes, so two “Always on” pills; the grant is attributed apart
    // from them.
    expect(screen.getAllByText('Always on')).toHaveLength(2);
    expect(screen.getByText('Allowed by a grant')).toBeInTheDocument();
    expect(screen.getByText(/Kigali manager is on shift/)).toBeInTheDocument();

    // “Why can this till not do that?” — the answer has to be readable, or it is
    // a support ticket.
    expect(screen.getByText('What this device cannot do')).toBeInTheDocument();
    expect(screen.getByText('menu:write')).toBeInTheDocument();
    expect(screen.getByText('Never granted to this device.')).toBeInTheDocument();
  });

  it('separates quarantine from revoke in the words it uses', async () => {
    const user = userEvent.setup();
    render(<DeviceFleet />);
    await user.click(await screen.findByRole('button', { name: 'Manage Till 2' }));

    await user.click(await screen.findByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(screen.getByText('Revoke this device for good?')).toBeInTheDocument());
    expect(
      screen.getByText(/A device that is lost, sold or stolen has to be replaced/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Its history stays on file/)).toBeInTheDocument();
  });

  it('warns before rotating a key, because the old one stops working at once', async () => {
    const user = userEvent.setup();
    render(<DeviceFleet />);
    await user.click(await screen.findByRole('button', { name: 'Manage Till 2' }));

    await user.click(await screen.findByRole('button', { name: 'Rotate the key' }));
    await waitFor(() => expect(screen.getByText('Replace this device’s key?')).toBeInTheDocument());
    expect(
      screen.getByText(/The key you have on the counter stops working the moment this finishes/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Yes, rotate the key' }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/devices/d1/rotate'));
    await waitFor(() => expect(screen.getByDisplayValue('dq_live_7f3c9a2b1d4e')).toBeInTheDocument());
  });

  it('reports being offline as offline, and retries on demand', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.reject(new ApiException(0, { code: 'network', message: 'Failed to fetch' }));
      }
      return routeGet(path);
    });
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByText('You are offline')).toBeInTheDocument());
    expect(screen.queryByText('No devices enrolled yet')).not.toBeInTheDocument();

    // The retry is a real read, not a reset of local state.
    mockGet.mockImplementation(routeGet);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Manage Till 2' })).toBeInTheDocument());
  });

  it('reports a refused read with the reference the operator can quote', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.reject(
          new ApiException(503, { code: 'unavailable', message: 'Fleet service is restarting' }),
        );
      }
      return routeGet(path);
    });
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByText('Could not load the device fleet')).toBeInTheDocument());
    expect(screen.getByText(/Fleet service is restarting/)).toBeInTheDocument();
  });

  it('never offers a revoked device a rotation or a pause, because it is re-enrolled', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.resolve({
          data: [{ ...DEVICES.data[1], status: 'revoked', label: 'Lost till', location_id: null }],
        });
      }
      return routeGet(path);
    });
    render(<DeviceFleet />);
    await user.click(await screen.findByRole('button', { name: 'Manage Lost till' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Rotate the key' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Quarantine' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Revoke' })).toBeDisabled();
      expect(screen.getByTitle('A revoked device is re-enrolled, not revived.')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Restore' })).not.toBeInTheDocument();
    });
  });
});
