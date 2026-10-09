import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { useState } from 'react';

// The loading screen used to advance four invented steps on a fixed 640ms
// timer, so it always took the same ~2.5s regardless of whether the workspace
// was ready. It now runs the reads the app would issue on first paint and
// reports each one as it finishes. These tests pin that behaviour: the real
// endpoints are called, the screen completes, and a failed read never traps the
// user on a spinner.

const refresh = vi.fn<[], Promise<{ permissions: string[] } | null>>();
let permissions: string[] = ['workspace:read', 'alerts:read'];

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
    refresh,
  }),
}));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { SigningIn } from '../pages/auth/SigningIn';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

const WAIT = { timeout: 3000 };

describe('SigningIn reflects real boot progress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permissions = ['workspace:read', 'alerts:read'];
    refresh.mockResolvedValue({ permissions });
    mockGet.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('issues the real readiness reads and then completes', async () => {
    const onComplete = vi.fn();
    render(<SigningIn userName="Olivia Owner" onComplete={onComplete} />);

    // The steps are the real ones, not scripted copy.
    expect(screen.getByText('Confirming your session')).toBeInTheDocument();
    expect(screen.getByText('Loading your workspace')).toBeInTheDocument();
    expect(screen.getByText('Checking for new alerts')).toBeInTheDocument();

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1), WAIT);
    expect(refresh).toHaveBeenCalled();
    expect(mockGet).toHaveBeenCalledWith('/api/v1/workspace');
    expect(mockGet).toHaveBeenCalledWith('/api/v1/alerts', expect.objectContaining({ status: 'unread' }));
  });

  it('does not fetch screens the seat cannot see, but still completes', async () => {
    permissions = ['forecasts:read'];
    refresh.mockResolvedValue({ permissions });
    const onComplete = vi.fn();
    render(<SigningIn userName="Grace Clerk" onComplete={onComplete} />);

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1), WAIT);
    expect(mockGet).not.toHaveBeenCalledWith('/api/v1/workspace');
    expect(mockGet).not.toHaveBeenCalledWith('/api/v1/alerts', expect.anything());
  });

  it('completes even when a readiness read fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const onComplete = vi.fn();
    render(<SigningIn userName="Olivia Owner" onComplete={onComplete} />);

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1), WAIT);
  });

  it('still completes when the parent remints onComplete during session hydrate', async () => {
    // Reproduces the production bug: App passed `() => setRoute('app')` inline,
    // refresh() updated SessionContext, App re-rendered with a new onComplete
    // identity, SigningIn's effect cleaned up (cancelled=true), and boot
    // restarted forever on "Confirming your session".
    let resolveRefresh!: (value: { permissions: string[] }) => void;
    refresh.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
    );

    function Harness() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <button type="button" onClick={() => setTick((n) => n + 1)}>
            remint
          </button>
          <SigningIn
            userName="Olivia Owner"
            onComplete={() => {
              // Intentionally unstable — new fn every render of Harness.
              void tick;
              (window as unknown as { __signingInDone?: number }).__signingInDone =
                ((window as unknown as { __signingInDone?: number }).__signingInDone ?? 0) + 1;
            }}
          />
        </>
      );
    }

    const { getByRole } = render(<Harness />);
    (window as unknown as { __signingInDone?: number }).__signingInDone = 0;

    // Mid-boot remint (what session hydrate used to trigger via App).
    await act(async () => {
      getByRole('button', { name: 'remint' }).click();
    });

    await act(async () => {
      resolveRefresh({ permissions: ['workspace:read', 'alerts:read'] });
    });

    await waitFor(
      () => expect((window as unknown as { __signingInDone?: number }).__signingInDone).toBe(1),
      WAIT,
    );
  });
});
