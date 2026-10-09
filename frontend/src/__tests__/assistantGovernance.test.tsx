import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, screen, waitFor } from '@testing-library/react';

/**
 * The Assistant Governance screen used to fetch in a loop.
 *
 * `fetchUsage` closed over `seats` via `useCallback(..., [seats])`, and `fetchAll`
 * depended on `fetchUsage`, while a `useEffect([fetchAll])` called `fetchAll`. Every
 * successful seats read produced a new array identity, so `fetchUsage` and `fetchAll`
 * were new functions on every pass and the effect re-ran immediately — the page
 * hammered `/admin/agent/quotas` and `/admin/agent/usage` for as long as it stayed
 * open. It surfaced first as an axe smoke-test timeout; this pins the real symptom
 * (a bounded number of reads) so a future dependency array cannot reintroduce it.
 */

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: { name: 'Olivia Owner', role: 'Owner', roleKey: 'owner', email: 'demo@demandiq.app', id: 'user-1', initials: 'OO', permissions: ['agent:read'] },
    isAuthenticated: true,
  }),
}));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed', code: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { AiGovernance } from '../pages/admin/AiGovernance';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/admin/agent/quotas') {
      return Promise.resolve({
        data: [{ user_id: 'user-2', name: 'Grace Clerk', email: 'clerk@demandiq.app', tier: 'operator', used_today: 12, used_month: 180, last_active: '2 hours ago', status: 'active' }],
      });
    }
    if (path === '/api/v1/admin/agent/usage') {
      return Promise.resolve({ data: [{ date: '2026-09-20', questions: 51, tool_calls: 110, refusals: 1 }] });
    }
    return Promise.resolve({ data: [] });
  });
});

function callsTo(path: string) {
  return mockGet.mock.calls.filter(([called]) => called === path).length;
}

describe('Assistant governance data loading', () => {
  it('caps the seat list so the panel does not grow with the team', async () => {
    render(<AiGovernance />);

    // One row per seat, so a large tenant would push every panel below it off
    // the screen. The list is capped and its header pinned instead.
    await waitFor(() => {
      const table = screen.getByRole('table', { name: /seat allowances/i });
      const box = table.parentElement as HTMLElement;
      expect(box.className).toContain('max-h-[420px]');
      expect(box.className).toContain('overflow-y-auto');
      expect(table.querySelector('thead th')?.className).toContain('sticky');
    });
  });

  it('reads each endpoint once per mount instead of looping', async () => {
    render(<AiGovernance />);
    for (let tick = 0; tick < 4; tick += 1) {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    }

    expect(callsTo('/api/v1/admin/agent/quotas')).toBe(1);
    expect(callsTo('/api/v1/admin/agent/usage')).toBe(1);
    expect(mockGet.mock.calls.length).toBeLessThan(8);
  });
});
