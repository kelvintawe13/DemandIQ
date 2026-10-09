import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// The seat that matters most here is the one that *asks*: the owner can request
// a view and cannot approve it, which is the whole point of the two-person rule.
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      id: 'u1',
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      permissions: ['devices:read', 'devices:manage', 'device_mirror:request'],
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
import { MirrorApprovals } from '../pages/MirrorApprovals';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const CONSENT_AT = '2026-09-10T08:00:00Z';

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
      scopes: ['sales:write'],
      status: 'active',
      last_seen_at: '2026-09-25T09:55:00Z',
      enrolled_at: '2026-08-01T10:00:00Z',
      enrolled_by: 'u1',
      revoked_at: null,
      mirroring_consent_at: CONSENT_AT,
    },
  ],
};

const ELIGIBLE = {
  data: {
    device_id: 'd1',
    eligible: true,
    reason: 'eligible',
    message: 'Mirroring is possible.',
    conditions: [
      { condition: 'customer_consent', ok: true, detail: 'Customer consent recorded at ...' },
      { condition: 'device_in_service', ok: true, detail: 'This device is in service.' },
      { condition: 'no_live_view', ok: true, detail: 'No mirror session is live on this device.' },
    ],
    consent_at: CONSENT_AT,
    active_session_id: null,
    can_request: true,
    can_approve: false,
    max_session_minutes: 30,
  },
};

const PENDING = {
  data: [
    {
      id: 'm1',
      device_id: 'd1',
      status: 'pending',
      requested_by: 'u2',
      decided_by: null,
      requested_at: '2026-09-25T09:30:00Z',
      decided_at: null,
      ends_at: null,
      ended_at: null,
      consent_at: CONSENT_AT,
      reason: 'Till 2 is showing an error on the payment screen and the shift is stuck',
      live: false,
      expired: false,
      // u1 cannot approve: no device_mirror:approve, and it is not theirs.
      approvable: false,
    },
  ],
  cannot_approve: [],
};

const MEMBERS = {
  data: [
    { id: 'u1', name: 'Olivia Owner' },
    { id: 'u2', name: 'Sam Server' },
  ],
};

function pendingOwnedByViewer() {
  return {
    data: [{ ...PENDING.data[0], requested_by: 'u1' }],
    cannot_approve: ['m1'],
  };
}

function routeGet(path: string) {
  if (path === '/api/v1/devices') return Promise.resolve(DEVICES);
  if (path === '/api/v1/members') return Promise.resolve(MEMBERS);
  if (path.includes('mirror-eligibility')) return Promise.resolve(ELIGIBLE);
  // The queue and a device's own history are two different routes, and the
  // queue's path also contains `/mirror-requests` — so it is matched first.
  if (path === '/api/v1/device-mirror/mirror-requests/pending') return Promise.resolve(PENDING);
  if (path.includes('/mirror-requests')) return Promise.resolve({ data: [] });
  return Promise.resolve({});
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  toast.mockReset();
  mockGet.mockImplementation(routeGet);
  mockPost.mockResolvedValue({ data: {} });
  Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  // See `devices.test.tsx`: `vi.restoreAllMocks` leaves `vi.fn()` mocks holding
  // their implementations, and Vitest calls a reset mock with no arguments
  // during restore — which fails the test if that mock rejects.
  mockGet.mockReset();
  mockPost.mockReset();
  toast.mockReset();
});

describe('Screen-mirroring approval queue', () => {
  it('states the four gates before it offers any of them', async () => {
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText('Before anybody can see a staff screen')).toBeInTheDocument());
    expect(screen.getByText('The customer has agreed')).toBeInTheDocument();
    expect(screen.getByText('Two different people')).toBeInTheDocument();
    expect(screen.getByText('Two different permissions')).toBeInTheDocument();
    expect(screen.getByText('It ends by itself')).toBeInTheDocument();
    // The rule that is easiest to get wrong, said in words.
    expect(
      screen.getByText(/Whoever asks can never be the person who agrees/),
    ).toBeInTheDocument();
  });

  it('shows a request with the reason the approver has to read', async () => {
    render(<MirrorApprovals />);

    expect(
      await screen.findByText(/Till 2 is showing an error on the payment screen/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Asked for by Sam Server/)).toBeInTheDocument();
    expect(screen.getByText('Needs a second person')).toBeInTheDocument();
    // Consent is quoted with its date, because "the customer agreed" without a
    // date is not a record.
    expect(screen.getByText(/The customer agreed to mirroring on/)).toBeInTheDocument();
  });

  it('offers no decision on a request this seat cannot make, and says which rule stopped it', async () => {
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText('Needs a second person')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Agree to let them watch/ })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Deciding needs device_mirror:approve/),
    ).toBeInTheDocument();
  });

  it('calls a requester’s own request their own, not a permission problem', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) return Promise.resolve(pendingOwnedByViewer());
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText(/Asked for by You/)).toBeInTheDocument());
    expect(screen.getByText(/This is your own request/)).toBeInTheDocument();
    expect(
      screen.queryByText(/Deciding needs device_mirror:approve/),
    ).not.toBeInTheDocument();
  });

  it('approves with the window the approver chose, never the requester’s', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) {
        return Promise.resolve({ data: [{ ...PENDING.data[0], approvable: true }], cannot_approve: [] });
      }
      return routeGet(path);
    });
    mockPost.mockResolvedValue({
      data: { ends_at: '2026-09-25T10:00:00Z', max_session_minutes: 30 },
    });

    render(<MirrorApprovals />);
    await user.click(await screen.findByRole('button', { name: /Agree to let them watch/ }));

    // The approver is told the view ends by itself, and is the one who says when.
    await waitFor(() => expect(screen.getByText('How long may the view last?')).toBeInTheDocument());
    await user.selectOptions(
      screen.getByLabelText('How long may the view last?'),
      '20',
    );
    await user.click(screen.getByRole('button', { name: 'Approve the view' }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/device-mirror/mirror-requests/m1/approve', {
        duration_minutes: 20,
      }),
    );
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('ends by itself'), 'info');
  });

  it('asks before refusing, and then tells the requester', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) {
        return Promise.resolve({ data: [{ ...PENDING.data[0], approvable: true }], cannot_approve: [] });
      }
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    await user.click(await screen.findByRole('button', { name: 'Refuse' }));
    await waitFor(() => expect(screen.getByText('Refuse this request?')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Refuse it' }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/device-mirror/mirror-requests/m1/deny', undefined),
    );
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('Refused'), 'info');
  });

  it('explains a 403 queue as the deliberate split, not a failure', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/device-mirror/mirror-requests/pending') {
        return Promise.reject(
          new ApiException(403, { code: 'forbidden', message: 'Insufficient permissions for this action' }),
        );
      }
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    // This seat holds device_mirror:request but not device_mirror:approve, so
    // the queue is refused — which is the design, and is said as such.
    expect(
      await screen.findByText('This seat cannot decide screen-mirroring requests'),
    ).toBeInTheDocument();
    expect(screen.getByText(/deliberately not given to the administrator role/)).toBeInTheDocument();
    // The rest of the screen still works.
    expect(screen.getByText('No staff screen is being viewed')).toBeInTheDocument();
    expect(screen.getByText('Consent recorded')).toBeInTheDocument();
  });

  it('has an empty queue and an empty live list that say different things', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText('Nobody is waiting for a second person')).toBeInTheDocument());
    expect(screen.getByText('No staff screen is being viewed')).toBeInTheDocument();
    expect(screen.getByText(/A view appears here the moment it is approved/)).toBeInTheDocument();
  });

  it('counts a live view down to the moment it ends by itself', async () => {
    const endsAt = new Date(Date.now() + 9 * 60_000).toISOString();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/mirror-requests') && !path.includes('pending')) {
        return Promise.resolve({
          data: [
            {
              id: 'm9',
              device_id: 'd1',
              status: 'active',
              requested_by: 'u2',
              decided_by: 'u3',
              requested_at: '2026-09-25T09:50:00Z',
              decided_at: '2026-09-25T09:51:00Z',
              ends_at: endsAt,
              ended_at: null,
              consent_at: CONSENT_AT,
              reason: 'Payment screen is frozen mid-service',
              live: true,
              expired: false,
              approvable: false,
            },
          ],
        });
      }
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });

    render(<MirrorApprovals />);

    await waitFor(() => {
      const list = screen.getByLabelText('Live views');
      expect(within(list).getByText('Live now')).toBeInTheDocument();
      expect(within(list).getByText(/opened by Sam Server · agreed by/i)).toBeInTheDocument();
      expect(within(list).getByText(/Ends at/)).toBeInTheDocument();
      expect(within(list).getByRole('button', { name: 'End this view' })).toBeInTheDocument();
      // A live view is counted down, never described as simply "active".
      expect(within(list).getByText(/^\d+m \d{2}s left$/)).toBeInTheDocument();
    });
  });

  it('ends a live view on request, and says the record stays', async () => {
    const user = userEvent.setup();
    const endsAt = new Date(Date.now() + 5 * 60_000).toISOString();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/mirror-requests') && !path.includes('pending')) {
        return Promise.resolve({
          data: [
            {
              id: 'm9',
              device_id: 'd1',
              status: 'active',
              requested_by: 'u2',
              decided_by: 'u3',
              requested_at: '2026-09-25T09:50:00Z',
              decided_at: '2026-09-25T09:51:00Z',
              ends_at: endsAt,
              ended_at: null,
              consent_at: CONSENT_AT,
              reason: 'Payment screen is frozen',
              live: true,
              expired: false,
              approvable: false,
            },
          ],
        });
      }
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });

    render(<MirrorApprovals />);
    await user.click(await screen.findByRole('button', { name: 'End this view' }));

    await waitFor(() => expect(screen.getByText('End this view now?')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'End it' }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/device-mirror/mirror-requests/m9/end', undefined),
    );
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('stays on file'), 'info');
  });

  it('does not offer to end a view whose window has already closed', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/mirror-requests') && !path.includes('pending')) {
        return Promise.resolve({
          data: [
            {
              id: 'm9',
              device_id: 'd1',
              status: 'active',
              requested_by: 'u2',
              decided_by: 'u3',
              requested_at: '2026-09-25T08:00:00Z',
              decided_at: '2026-09-25T08:01:00Z',
              ends_at: '2026-09-25T08:31:00Z',
              ended_at: null,
              consent_at: CONSENT_AT,
              reason: 'Payment screen is frozen',
              // The server says it is over even though the row still says active.
              live: false,
              expired: true,
              approvable: false,
            },
          ],
        });
      }
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });

    render(<MirrorApprovals />);

    // `live` is computed from ends_at, not read from status — a closed window is
    // not a view, and claiming otherwise is the bug this guards.
    await waitFor(() => expect(screen.getByText('No staff screen is being viewed')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'End this view' })).not.toBeInTheDocument();
  });

  it('records consent with the date it was given', async () => {
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText('Consent recorded')).toBeInTheDocument());
    expect(screen.getByText(new RegExp(`The customer agreed on`))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw consent' })).toBeInTheDocument();
    // The gates the server reported, each attributed.
    expect(screen.getByText('The customer agreed to this:')).toBeInTheDocument();
    expect(screen.getByText('The device is in service:')).toBeInTheDocument();
    expect(screen.getByText('Nobody else is watching that screen:')).toBeInTheDocument();
  });

  it('says withdrawing consent ends the views it was supporting, and counts them', async () => {
    const user = userEvent.setup();
    mockPost.mockResolvedValue({
      data: { device_id: 'd1', granted: false, consent_at: null, sessions_ended: 2 },
    });
    render(<MirrorApprovals />);

    await user.click(await screen.findByRole('button', { name: 'Withdraw consent' }));
    expect(
      await screen.findByText('Withdraw consent for Till 2?'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Any live view of that screen ends immediately/),
    ).toBeInTheDocument();

    // Two buttons carry that name from here: the row's, and the confirmation's.
    await user.click(screen.getAllByRole('button', { name: 'Withdraw consent' })[1]);

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/device-mirror/devices/d1/mirror-consent',
        { granted: false },
      ),
    );
    expect(toast).toHaveBeenCalledWith(
      expect.stringContaining('2 live or queued sessions were ended'),
      'info',
    );
  });

  it('records consent where there is none, and opens the request path', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.resolve({
          data: [{ ...DEVICES.data[0], mirroring_consent_at: null }],
        });
      }
      if (path.includes('mirror-eligibility')) {
        return Promise.resolve({
          data: {
            ...ELIGIBLE.data,
            eligible: false,
            reason: 'no_consent',
            message:
              'The customer has not consented to screen mirroring for this device. Record their consent first; without it no view may be requested.',
            consent_at: null,
            conditions: [
              { condition: 'customer_consent', ok: false, detail: 'The customer has not consented to screen mirroring for this device.' },
              { condition: 'device_in_service', ok: true, detail: 'This device is in service.' },
              { condition: 'no_live_view', ok: true, detail: 'No mirror session is live on this device.' },
            ],
          },
        });
      }
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    // The refusal is explained rather than leaving a button that does nothing.
    await waitFor(() => expect(screen.getByText('No consent')).toBeInTheDocument());
    expect(
      screen.getByText(/Record their consent first; without it no view may be requested/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Ask to watch this screen' }),
    ).not.toBeInTheDocument();

    mockPost.mockResolvedValue({ data: { device_id: 'd1', granted: true, consent_at: CONSENT_AT, sessions_ended: 0 } });
    await user.click(screen.getByRole('button', { name: 'Record the customer’s consent' }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/device-mirror/devices/d1/mirror-consent', {
        granted: true,
      }),
    );
  });

  it('raises a request with a reason and nothing else, then shows what a 201 means', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    await user.click(await screen.findByRole('button', { name: 'Ask to watch this screen' }));
    await waitFor(() => expect(screen.getByText('Ask to watch Till 2')).toBeInTheDocument());
    // The requester is told, before sending, that they are not deciding.
    expect(
      screen.getByText(/You are asking, not deciding/),
    ).toBeInTheDocument();

    await user.type(
      screen.getByLabelText('Why does the screen need to be seen?'),
      'Till 2 is stuck on the payment screen',
    );
    mockPost.mockResolvedValue({
      data: {
        id: 'm2',
        device_id: 'd1',
        awaiting_second_person: true,
        notice:
          'This request views nothing yet. A second workspace member holding device_mirror:approve must approve it first, and you cannot approve your own request.',
      },
    });
    await user.click(screen.getByRole('button', { name: 'Send the request' }));

    // `duration_minutes` is the approver's field, and the router refuses it here.
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/device-mirror/devices/d1/mirror-requests', {
        reason: 'Till 2 is stuck on the payment screen',
      }),
    );
    await waitFor(() => expect(screen.getByText('Nothing is being viewed yet')).toBeInTheDocument());
    expect(screen.getByText(/must approve it first, and you cannot approve your own/)).toBeInTheDocument();
  });

  it('will not send a request without a reason', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });
    render(<MirrorApprovals />);
    await user.click(await screen.findByRole('button', { name: 'Ask to watch this screen' }));
    await user.click(await screen.findByRole('button', { name: 'Send the request' }));

    expect(
      await screen.findByText(/Say why the screen is being viewed/),
    ).toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('reports a refusal in the operator’s words, not as “Request failed”', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) {
        return Promise.resolve({ data: [{ ...PENDING.data[0], approvable: true }], cannot_approve: [] });
      }
      return routeGet(path);
    });
    mockPost.mockRejectedValue(
      new ApiException(409, { code: 'two_person_rule', message: 'A mirror request must be decided by a second person.' }),
    );
    render(<MirrorApprovals />);

    await user.click(await screen.findByRole('button', { name: /Agree to let them watch/ }));
    await user.click(await screen.findByRole('button', { name: 'Approve the view' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'A second person has to agree. You cannot decide your own request.',
        'error',
      ),
    );
  });

  it('narrows the consent list by name, and says when nothing matches', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('mirror-requests/pending')) return Promise.resolve({ data: [], cannot_approve: [] });
      return routeGet(path);
    });
    render(<MirrorApprovals />);
    await screen.findByText('Consent recorded');

    await user.type(screen.getByLabelText('Find a device by name'), 'Bar');
    await waitFor(() => expect(screen.getByText('No device matches that')).toBeInTheDocument());
    expect(screen.getByText(/Nothing in this workspace is called “Bar”/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Clear the search' }));
    await waitFor(() => expect(screen.getByText('Consent recorded')).toBeInTheDocument());
  });

  it('reports being offline as offline rather than as an empty queue', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.reject(new ApiException(0, { code: 'network', message: 'Failed to fetch' }));
      }
      return routeGet(path);
    });
    render(<MirrorApprovals />);

    await waitFor(() => expect(screen.getByText('You are offline')).toBeInTheDocument());
    expect(screen.queryByText('Nobody is waiting for a second person')).not.toBeInTheDocument();
  });
});
