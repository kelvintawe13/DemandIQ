import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * Turning a validation rule on or off did nothing at all.
 *
 * `Toggle` reports the value it should move *to* (`onChange(!checked)`) — that is
 * its documented contract, and the suite in `mobilePrimitives.test.tsx` pins it.
 * The Quality Monitor ignored the argument and passed `rule.enabled`, the value
 * the switch was already showing, into `toggleRule(id, nextEnabled)` — which
 * writes what it is handed. So every click re-sent the current state: the switch
 * flicked back and the PATCH carried no change.
 *
 * These tests assert the request that leaves the browser, not just that a click
 * happened, because that is what "does not work" looked like from the outside.
 */

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      email: 'owner@demandiq.app',
      id: 'user-1',
      initials: 'OO',
      permissions: ['quality:read', 'quality:fix', 'quality:configure'],
    },
    isAuthenticated: true,
  }),
}));
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
import { QualityMonitor } from '../pages/QualityMonitor';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPatch = api.patch as unknown as ReturnType<typeof vi.fn>;

const RULE = {
  id: 'qrule-1',
  name: 'spike_detection',
  description: 'Flags implausible spikes',
  enabled: true,
};

/** The page's four reads, with the rule list under test. */
function withRules(rules: Array<Record<string, unknown>>) {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/quality/rules') return Promise.resolve({ data: rules });
    if (path === '/api/v1/quality/score') {
      return Promise.resolve({ overall: 88, dimensions: { completeness: 92, timeliness: 84 } });
    }
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPatch.mockReset();
  mockPatch.mockResolvedValue({});
  withRules([{ ...RULE }]);
});

describe('Validation rule switches', () => {
  it('writes the value the switch moved to, not the one it was showing', async () => {
    const user = userEvent.setup();
    render(<QualityMonitor />);

    const toggle = await screen.findByRole('switch', { name: 'spike_detection' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    await user.click(toggle);

    expect(mockPatch).toHaveBeenCalledWith('/api/v1/quality/rules/qrule-1', { enabled: false });
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'false'));
  });

  it('turns the rule back on from the same control', async () => {
    const user = userEvent.setup();
    withRules([{ ...RULE, enabled: false }]);
    render(<QualityMonitor />);

    const toggle = await screen.findByRole('switch', { name: 'spike_detection' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');

    await user.click(toggle);

    expect(mockPatch).toHaveBeenCalledWith('/api/v1/quality/rules/qrule-1', { enabled: true });
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
  });

  it('puts the switch back when the API refuses the change', async () => {
    const user = userEvent.setup();
    const { ApiException } = await import('../api/client');
    mockPatch.mockRejectedValue(new (ApiException as any)('nope'));
    render(<QualityMonitor />);

    const toggle = await screen.findByRole('switch', { name: 'spike_detection' });
    await user.click(toggle);

    // The optimistic flip must not survive a rejected write — the switch states
    // what the server holds, and the server still holds `on`.
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
  });
});
