import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

// The hook reads the session role to decide which badges to fetch; a mutable
// object lets a test switch roles mid-flight, like the demo seat placeholder
// giving way to the hydrated session.
const session: { user: { id: string; role: string; isSiteAdmin: boolean } } = {
  // The hook only fetches once /auth/me has hydrated (`user.id` present), so
  // the fixture carries a seat id — the id-less placeholder is a separate case.
  user: { id: 'seat-1', role: 'Data Clerk', isSiteAdmin: false },
};
vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));
vi.mock('../api/client', () => ({ api: { get: vi.fn() } }));

import { api } from '../api/client';
import { useNavBadges, notifyNavBadgesChanged } from '../hooks/useNavBadges';

function Harness() {
  const badges = useNavBadges();
  return <pre data-testid="badges">{JSON.stringify(badges)}</pre>;
}

const readings = () => JSON.parse(screen.getByTestId('badges').textContent ?? '{}');

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe('useNavBadges', () => {
  beforeEach(() => {
    session.user = { id: 'seat-1', role: 'Data Clerk', isSiteAdmin: false };
    mockGet.mockReset();
    mockGet.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reports the exact unread count for the alerts badge', async () => {
    mockGet.mockImplementation((path: string) =>
      path.includes('/alerts') ? Promise.resolve({ unread_count: 7 }) : Promise.resolve({ data: [] }));
    render(<Harness />);
    await waitFor(() => expect(readings()).toEqual({ alerts: 7 }));
    expect(mockGet).toHaveBeenCalledWith('/api/v1/alerts', { status: 'unread', limit: 1 });
  });

  it('counts only pending change requests for the approvals badge', async () => {
    session.user = { id: 'seat-1', role: 'Administrator', isSiteAdmin: false };
    mockGet.mockImplementation((path: string) =>
      path.includes('/change-requests')
        ? Promise.resolve({ data: [{ status: 'pending' }, { status: 'approved' }, { status: 'pending' }] })
        : Promise.resolve({ unread_count: 0 }));
    render(<Harness />);
    // An administrator sees the workspace screens as well as the console, so
    // both badges are fetched (the approved row must not be counted).
    await waitFor(() => expect(readings()).toEqual({ alerts: 0, 'admin-approvals': 2 }));
  });

  it('does not fetch the badges a role cannot see', async () => {
    session.user = { id: 'seat-1', role: 'Data Clerk', isSiteAdmin: false };
    mockGet.mockImplementation((path: string) =>
      path.includes('/alerts') ? Promise.resolve({ unread_count: 1 }) : Promise.resolve({ data: [{ status: 'pending' }] }));
    render(<Harness />);
    await waitFor(() => expect(readings()).toEqual({ alerts: 1 }));
    expect(mockGet.mock.calls.map((c) => c[0])).not.toContain('/api/v1/change-requests');
  });

  it('keeps a fresh badge when a superseded fetch resolves late', async () => {
    // Regression: the role switch from the placeholder seat made the hook start
    // a second load. With the second load answering fast and the first one
    // arriving late, the stale run replaced the counts wholesale, so a badge
    // vanished until the next poll.
    let releaseAlerts: (value: unknown) => void = () => undefined;
    const held = new Promise((resolve) => {
      releaseAlerts = resolve;
    });
    let alertsCalls = 0;
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/alerts')) {
        alertsCalls += 1;
        // Data Clerk's load hangs; the administrator's load answers at once.
        return alertsCalls === 1 ? held : Promise.resolve({ unread_count: 0 });
      }
      return Promise.resolve({ data: [{ status: 'pending' }, { status: 'pending' }] });
    });

    const { rerender } = render(<Harness />);
    await waitFor(() => expect(alertsCalls).toBe(1));

    session.user = { id: 'seat-1', role: 'Administrator', isSiteAdmin: false };
    rerender(<Harness />);
    await waitFor(() =>
      expect(readings()).toEqual({ alerts: 0, 'admin-approvals': 2 }));

    await act(async () => {
      releaseAlerts({ unread_count: 9 });
      await Promise.resolve();
    });
    // The abandoned clerk load must not overwrite the administrator's counts.
    expect(readings()).toEqual({ alerts: 0, 'admin-approvals': 2 });
  });

  it('does not fetch before the session hydrates', async () => {
    // The placeholder seat renders one frame before /auth/me answers; it has no
    // id, and fetching then issued calls the signed-in user may not make.
    session.user = { id: '', role: 'Owner', isSiteAdmin: false };
    render(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockGet).not.toHaveBeenCalled();
    expect(readings()).toEqual({});
  });

  it('shows no badge rather than a number when the request fails', async () => {
    mockGet.mockRejectedValue(new Error('offline'));
    render(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(readings()).toEqual({});
  });

  it('re-reads the counts when something reports a change', async () => {
    let unread = 1;
    mockGet.mockImplementation((path: string) =>
      path.includes('/alerts') ? Promise.resolve({ unread_count: unread }) : Promise.resolve({ data: [] }));
    render(<Harness />);
    await waitFor(() => expect(readings()).toEqual({ alerts: 1 }));

    unread = 0;
    act(() => {
      notifyNavBadgesChanged();
    });
    await waitFor(() => expect(readings()).toEqual({ alerts: 0 }));
  });
});
