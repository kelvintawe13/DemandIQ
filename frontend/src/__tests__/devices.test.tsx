/**
 * The Devices & sessions screen.
 *
 * It is the one place the whole device list is the point, so these cover what a
 * seat can do from it: see every signed-in session, tell which one it is looking
 * through, sign the others out — and never lose the ability to sign out a
 * device that happens to be remembered (`trusted_device`).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const session = {
  user: {
    name: 'Olivia Owner',
    role: 'Owner',
    roleKey: 'owner',
    email: 'demo@demandiq.app',
    id: 'user-1',
    initials: 'OO',
    mfaEnabled: true,
    permissions: ['workspace:read'],
  },
};

vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { Devices } from '../pages/Devices';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockDelete = api.delete as unknown as ReturnType<typeof vi.fn>;

const ISO = '2026-09-20T08:00:00Z';

/**
 * The rows of the device list.
 *
 * Scoped to the labelled list on purpose: the right-hand column is a list too,
 * and two of its items explain what "This device" and "Trusted device" mean,
 * so an unscoped text query finds the explanation as well as the badge.
 */
const sessionRows = () =>
  within(screen.getByRole('list', { name: 'Signed-in devices' })).getAllByRole('listitem');

const rowFor = (device: string) => {
  const row = sessionRows().find((item) => within(item).queryByText(device));
  if (!row) throw new Error(`no session row for ${device}`);
  return row;
};

/** The payload `GET /api/v1/me/sessions` returns. */
const SESSIONS = {
  data: [
    { id: 'sess-mine', device: 'MacBook Pro', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO, current: true },
    { id: 'sess-phone', device: 'iPhone', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO, current: false },
    { id: 'sess-other', device: 'Windows PC', origin: '127.0.0.2', trusted_device: false, last_seen_at: ISO, current: false },
  ],
};

beforeEach(() => {
  mockGet.mockReset();
  mockDelete.mockReset();
  mockGet.mockImplementation(() => Promise.resolve(SESSIONS));
  mockDelete.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  // `vi.restoreAllMocks` only restores `vi.spyOn` spies, so the `vi.fn()` mocks
  // still hold whatever a failure-path test installed. Vitest then invokes a
  // reset mock with no arguments during its restore step, and a mock that
  // rejects would fail the test *after* its assertions passed. Clearing the
  // implementations here means that probe gets a harmless `undefined`.
  mockGet.mockReset();
  mockDelete.mockReset();
});

describe('Devices & sessions', () => {
  it('lists every signed-in session with where it signed in from', async () => {
    render(<Devices onNavigate={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('MacBook Pro')).toBeInTheDocument());
    expect(screen.getByText('iPhone')).toBeInTheDocument();
    expect(screen.getByText('Windows PC')).toBeInTheDocument();
    // The origin is stated as where the sign-in came from, not called a country.
    expect(screen.getAllByText(/Signed in from 127\.0\.0\.1/)).toHaveLength(2);
    expect(screen.getByText(/Signed in from 127\.0\.0\.2/)).toBeInTheDocument();
  });

  it('labels the session it is being read through, and does not offer to end it', async () => {
    render(<Devices onNavigate={vi.fn()} />);

    // `current` comes from the token's `sid` claim, so exactly one session row
    // is the browser showing the page — and it is not offered for sign-out.
    await waitFor(() => {
      const current = sessionRows().filter((row) => within(row).queryByText('This device'));
      expect(current).toHaveLength(1);
      expect(within(current[0]).queryByRole('button', { name: /^Sign out / })).toBeNull();
    });

    // The other two can be signed out.
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /^Sign out / })).toHaveLength(2);
      expect(screen.queryByRole('button', { name: 'Sign out MacBook Pro' })).toBeNull();
    });
  });

  it('keeps a remembered device revocable: Trusted is a badge, not a lock', async () => {
    render(<Devices onNavigate={vi.fn()} />);

    // `trusted_device` used to stand in for the revoke control, so a remembered
    // device could never be signed out from anywhere in the app.
    await waitFor(() => {
      const iphone = rowFor('iPhone');
      expect(within(iphone).getByText('Trusted device')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Sign out iPhone' })).toBeEnabled();
    });
  });

  it('confirms before ending a session, then deletes it and drops the row', async () => {
    const user = userEvent.setup();
    render(<Devices onNavigate={vi.fn()} />);

    await user.click(await screen.findByRole('button', { name: 'Sign out iPhone' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/will need to sign in again/)).toBeInTheDocument();
    // Nothing is sent until the confirmation is accepted.
    expect(mockDelete).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/api/v1/me/sessions/sess-phone'));
    await waitFor(() => expect(screen.queryByText('iPhone')).toBeNull());
    // The rest of the list survives.
    expect(screen.getByText('Windows PC')).toBeInTheDocument();
  });

  it('shows a row the API did not identify without offering to delete it', async () => {
    mockGet.mockImplementation(() =>
      Promise.resolve({ data: [{ device: 'Unknown browser', last_seen_at: ISO, current: false }] }),
    );
    render(<Devices onNavigate={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('Unknown browser')).toBeInTheDocument());
    // No id means no DELETE to send: the mapping used to fabricate one, so the
    // button asked the API to revoke a session that did not exist.
    expect(screen.queryByRole('button', { name: /^Sign out / })).toBeNull();
    expect(screen.getByText('Not revocable')).toBeInTheDocument();
  });

  it('states the account’s two-factor state and links to security settings', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<Devices onNavigate={onNavigate} />);

    await waitFor(() => expect(screen.getByText('Two-factor authentication')).toBeInTheDocument());
    expect(screen.getByText('Enabled')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Security settings' }));
    expect(onNavigate).toHaveBeenCalledWith('settings');
  });

  it('offers a retry when the list cannot be read', async () => {
    const user = userEvent.setup();
    mockGet.mockRejectedValue(new TypeError('Failed to fetch'));
    render(<Devices onNavigate={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('You are offline')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('says there are no sessions rather than showing an empty panel', async () => {
    mockGet.mockImplementation(() => Promise.resolve({ data: [] }));
    render(<Devices onNavigate={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('No signed-in devices')).toBeInTheDocument());
  });
});
