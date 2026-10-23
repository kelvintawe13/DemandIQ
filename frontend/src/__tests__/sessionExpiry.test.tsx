/**
 * Session-expiry UX tests: the bridge, the context toast, and the App-level
 * redirect with an explanatory notice on the sign-in screen.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

import {
  notifySessionExpired,
  onSessionExpired,
  SESSION_EXPIRED_MESSAGE,
} from '../lib/sessionExpiry';
import { SessionProvider } from '../contexts/SessionContext';
import { ToastProvider, useToast } from '../contexts/ToastContext';
import { App } from '../App';
import { api } from '../api/client';
import type { SessionUser } from '../contexts/SessionContext';

/** A 401 body in the shape the API client unwraps. */
function unauthenticated(): Response {
  return new Response(JSON.stringify({ error: { code: 'unauthenticated', message: 'Token expired' } }), {
    status: 401,
    headers: { 'content-type': 'application/json' },
  });
}

const OPERATOR: SessionUser = {
  email: 'aline@kigali-bistro.rw',
  name: 'Aline Umutoni',
  role: 'Data Clerk',
  roleKey: 'operator',
  initials: 'AU',
};

describe('sessionExpiry bridge', () => {
  it('notifies subscribers exactly once per tick (dedupe guard)', () => {
    const handler = vi.fn();
    const unsub = onSessionExpired(handler);
    act(() => {
      notifySessionExpired();
      notifySessionExpired();
      notifySessionExpired();
    });
    expect(handler).toHaveBeenCalledTimes(1);
    unsub();
  });

  it('unsubscribe stops delivery', () => {
    const handler = vi.fn();
    const unsub = onSessionExpired(handler);
    unsub();
    act(() => notifySessionExpired());
    expect(handler).not.toHaveBeenCalled();
  });
});

function ToastProbe() {
  const { toast } = useToast();
  return (
    <button type="button" onClick={() => toast('seed', 'info')}>
      seed
    </button>
  );
}

function SessionHost() {
  // Provider order matches App: ToastProvider wraps SessionProvider.
  return (
    <ToastProvider>
      <SessionProvider user={OPERATOR} signOut={() => undefined}>
        <ToastProbe />
      </SessionProvider>
    </ToastProvider>
  );
}

describe('SessionContext expiry toast', () => {
  beforeEach(() => {
    try {
      localStorage.removeItem('demandiq_token');
      localStorage.removeItem('demandiq_refresh_token');
    } catch {}
  });

  it('shows the Bootstrap-style expiry toast while a user is active', async () => {
    render(<SessionHost />);
    act(() => {
      screen.getByText('seed').click();
    });
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    act(() => {
      notifySessionExpired();
    });
    await waitFor(() => {
      expect(screen.getByText(SESSION_EXPIRED_MESSAGE)).toBeInTheDocument();
    });
    // The expiry toast itself is a Bootstrap-style role=alert region.
    expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
  });

  it('stays silent when no user session is active (landing screen)', () => {
    render(
      <ToastProvider>
        <SessionProvider
          user={{ name: '', role: '', roleKey: '', email: '', initials: '' }}
          signOut={() => undefined}
        >
          <ToastProbe />
        </SessionProvider>
      </ToastProvider>,
    );
    act(() => {
      notifySessionExpired();
    });
    expect(screen.queryByText(SESSION_EXPIRED_MESSAGE)).not.toBeInTheDocument();
  });
});

describe('API client: an unrecoverable 401 tears the session down', () => {
  beforeEach(() => {
    localStorage.removeItem('demandiq_token');
    localStorage.removeItem('demandiq_refresh_token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.removeItem('demandiq_token');
    localStorage.removeItem('demandiq_refresh_token');
  });

  it('clears the dead token and raises expiry even with no refresh token', async () => {
    // The teardown used to sit behind `getRefreshToken()`, so a session with no
    // refresh credential fell through: the stale token stayed in localStorage
    // and the shell stayed mounted around failing panels, with nothing able to
    // leave it. This is the regression guard for that dead end.
    // The bridge coalesces events raised inside one tick, and the tests above
    // raise their own: start on a clean tick so this expiry is not swallowed by
    // that window rather than by the teardown.
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    localStorage.setItem('demandiq_token', 'expired-access-token');
    const onExpired = vi.fn();
    const unsub = onSessionExpired(onExpired);
    vi.stubGlobal('fetch', vi.fn(async () => unauthenticated()));

    await expect(api.get('/api/v1/locations')).rejects.toThrow(/Token expired/);

    expect(localStorage.getItem('demandiq_token')).toBeNull();
    await waitFor(() => expect(onExpired).toHaveBeenCalled());
    unsub();
  });

  it('leaves the session alone when the sign-in request itself 401s', async () => {
    // A wrong password is not an expired session: redirecting here would turn a
    // typo into a loop back to the form the user is already on.
    localStorage.setItem('demandiq_token', 'live-token');
    vi.stubGlobal('fetch', vi.fn(async () => unauthenticated()));

    await expect(api.post('/api/v1/auth/login', { email: 'a@b.c', password: 'nope' })).rejects.toThrow();

    expect(localStorage.getItem('demandiq_token')).toBe('live-token');
  });
});

describe('App boot with no credential', () => {
  const start = window.location.pathname;

  afterEach(() => {
    try {
      localStorage.removeItem('demandiq_token');
      localStorage.removeItem('demandiq_refresh_token');
    } catch {}
    window.history.replaceState(null, '', start);
  });

  it('sends a console link with no token to sign-in, not to a broken shell', async () => {
    window.history.replaceState(null, '', '/analytics');
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Welcome back' })).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/login');
  });
});

describe('App redirect on unrecoverable expiry', () => {
  beforeEach(() => {
    try {
      localStorage.removeItem('demandiq_token');
      localStorage.removeItem('demandiq_refresh_token');
    } catch {}
  });

  it('routes to sign-in with the expiry notice (never bare "undefined")', async () => {
    render(<App startAuthenticated />);

    // Simulate the API client reporting an unrecoverable 401.
    act(() => {
      window.dispatchEvent(new CustomEvent('demandiq:session-expired'));
    });

    // Expiry redirect lands on the sign-in screen with the explanatory
    // notice (the toast may also be visible — both are intentional).
    await waitFor(() => {
      expect(screen.getAllByText(SESSION_EXPIRED_MESSAGE).length).toBeGreaterThan(0);
    });
    expect(screen.getByRole('heading', { name: 'Welcome back' })).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });
});
