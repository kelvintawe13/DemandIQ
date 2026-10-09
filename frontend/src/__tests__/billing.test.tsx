import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// Billing is a subscriber's console: if it wrongly decides billing is off, the
// page shows "Billing not configured" and every plan, invoice and usage figure
// disappears behind an empty state. These tests pin the probe that decides it.
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      id: 'u1',
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      permissions: ['billing:manage'],
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

import { api } from '../api/client';
import { Billing } from '../pages/Billing';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const PLANS = {
  data: [
    {
      id: 'starter',
      name: 'Starter',
      price: 'RWF 0',
      price_cents: 0,
      cadence: 'forever',
      features: ['One location', 'Email support'],
    },
    {
      id: 'growth',
      name: 'Growth',
      price: 'RWF 48,000',
      price_cents: 4800000,
      cadence: 'monthly',
      features: ['Up to 10 locations', 'Model registry', 'Priority support'],
    },
  ],
};
const SUBSCRIPTION = { id: 'sub1', plan_id: 'growth', status: 'active', current_period_end: null };
const USAGE = { usage: [{ label: 'Locations', used: 1, limit: 10, unit: '' }], plan: {}, subscription: {} };
const INVOICES = { data: [{ id: 'INV-2026-09', period: 'Sep 2026', amount: 'RWF 48,000', status: 'paid' }] };
const CONTACT = { email: 'finance@demandiq.app', tin: '102938475' };
const PAYMENT = { brand: 'visa', last4: '4242', exp_month: 9, exp_year: 2028, is_default: true };

/** Every billing read resolves successfully. */
function allHealthy() {
  mockGet.mockImplementation(async (path: string) => {
    if (path.endsWith('/billing/status')) return { enabled: true };
    if (path.endsWith('/billing/plans')) return PLANS;
    if (path.endsWith('/billing/subscription')) return SUBSCRIPTION;
    if (path.endsWith('/billing/usage')) return USAGE;
    if (path.endsWith('/billing/invoices')) return INVOICES;
    if (path.endsWith('/billing/contact')) return CONTACT;
    if (path.endsWith('/billing/payment-method')) return PAYMENT;
    return {};
  });
}

describe('Billing availability probe', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    allHealthy();
  });

  afterEach(() => {
    // Reset in afterEach, not beforeEach: vitest invokes a cleared mock with no
    // arguments during restore, and a rejected implementation left over from a
    // previous test would then fail this one after its assertions passed.
    mockGet.mockReset();
    mockPost.mockReset();
    vi.useRealTimers();
  });

  it('keeps showing the console after the probe settles', async () => {
    // The bug this pins: the page rendered the real console, then flipped to
    // "Billing not configured" once a watchdog fired. A successful probe has to
    // be final.
    render(<Billing />);

    await waitFor(() => expect(screen.getByText('Growth')).toBeTruthy());
    expect(screen.queryByText('Billing not configured')).toBeNull();

    // Well past the old 900ms watchdog.
    await vi.advanceTimersByTimeAsync(2000);

    expect(screen.queryByText('Billing not configured')).toBeNull();
    expect(screen.getByText('Growth')).toBeTruthy();
  });

  it('shows the console immediately when the probe answers at once', async () => {
    render(<Billing />);
    await waitFor(() => expect(screen.getByText('Growth')).toBeTruthy());
    await vi.advanceTimersByTimeAsync(1500);
    expect(screen.queryByText('Billing not configured')).toBeNull();
  });

  it('shows the empty state when billing really is not configured', async () => {
    mockGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/billing/status')) throw new Error('404');
      return {};
    });

    render(<Billing />);

    await waitFor(() => expect(screen.getByText('Billing not configured')).toBeTruthy());
  });

  it('does not fetch the rest of the console when billing is off', async () => {
    mockGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/billing/status')) throw new Error('404');
      return {};
    });

    render(<Billing />);
    await waitFor(() => expect(screen.getByText('Billing not configured')).toBeTruthy());

    const paths = mockGet.mock.calls.map((c) => c[0] as string);
    expect(paths).not.toContain('/api/v1/billing/plans');
    expect(paths).not.toContain('/api/v1/billing/invoices');
  });
});
