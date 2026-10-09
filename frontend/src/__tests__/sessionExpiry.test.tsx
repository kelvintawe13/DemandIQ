/**
 * Session-expiry UX tests: the bridge, the context toast, and the App-level
 * redirect with an explanatory notice on the sign-in screen.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
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
import type { SessionUser } from '../contexts/SessionContext';

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
