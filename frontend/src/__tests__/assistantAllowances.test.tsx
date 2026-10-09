/**
 * The assistant administration screen states the allowances the API enforces.
 *
 * This screen used to carry its own table of tiers in `data/agentAdmin.ts`, which
 * had drifted from the server in three ways:
 *
 * * numbers — `monthly` 1200 against an enforced 600, `concurrent` 3 against 5;
 * * a `viewer` tier the backend has never had (a viewer is served by the
 *   fallback tier), shown as 20/day against an enforced 60;
 * * a tier key `admin` where the backend says `administrator`, so
 *   `tierOf('administrator')` missed and an administrator's allowance was
 *   displayed as the Data Clerk's.
 *
 * The figures now come from `GET /api/v1/admin/agent/tiers`, which reads them out
 * of the same tables the request path enforces. These tests render the screen
 * against a payload whose numbers differ from the old local table, so a
 * regression to a local table fails here rather than only on screen.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
import { AiGovernance } from '../pages/admin/AiGovernance';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

/** The payload `GET /api/v1/admin/agent/tiers` really returns (routers/agent.py). */
const TIERS = {
  data: [
    {
      id: 'operator',
      name: 'Data Clerk',
      daily: 60,
      monthly: 600,
      concurrent: 2,
      tools: ['forecast', 'history', 'explain', 'policy', 'pipeline'],
    },
    {
      id: 'analyst',
      name: 'Analyst',
      daily: 150,
      monthly: 1500,
      concurrent: 5,
      tools: ['forecast', 'history', 'explain', 'policy', 'pipeline', 'model'],
    },
    {
      id: 'administrator',
      name: 'Administrator',
      daily: 200,
      monthly: 2000,
      concurrent: 10,
      tools: ['*'],
    },
    { id: 'owner', name: 'Owner', daily: 200, monthly: 2000, concurrent: 10, tools: ['*'] },
  ],
  fallback_tier: 'operator',
  session: { idle_timeout_min: 30, max_duration_h: 4, turns: 40, burst_per_min: 6 },
};

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) =>
    path === '/api/v1/admin/agent/tiers'
      ? Promise.resolve(TIERS)
      : Promise.resolve({ data: [] }),
  );
});

describe('assistant administration allowances', () => {
  it('reads the tier list from the API', async () => {
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());
    expect(mockGet).toHaveBeenCalledWith('/api/v1/admin/agent/tiers');
  });

  it('states the enforced numbers, not the ones the old local table carried', async () => {
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());

    // Enforced: analyst monthly 1500. The retired local table said 3000.
    expect(screen.getByText('1500')).toBeInTheDocument();
    expect(screen.queryByText('3000')).toBeNull();
    // Enforced: administrator daily 200. The retired local table said 300.
    expect(screen.getAllByText('200').length).toBeGreaterThan(0);
    expect(screen.queryByText('300')).toBeNull();
  });

  it('does not invent a viewer tier the backend has never had', async () => {
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());
    // The API lists four tiers; a viewer is served by the fallback tier instead.
    expect(screen.queryByText('Viewer')).toBeNull();
    expect(screen.getByText('Data Clerk')).toBeInTheDocument();
    expect(screen.getByText('Owner')).toBeInTheDocument();
  });

  it('names a tier the backend keys differently from the old table', async () => {
    // The backend says `administrator`, never `admin`; the local table used `admin`,
    // so an administrator's seat fell through to the Data Clerk's allowance.
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());
    expect(screen.getByText('Administrator')).toBeInTheDocument();
  });

  it('renders tool tokens as words, including the wildcard', async () => {
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());
    // `*` used to render as a literal asterisk chip.
    expect(screen.queryByText('*')).toBeNull();
    expect(screen.getAllByText('Everything').length).toBe(2); // administrator + owner
    expect(screen.getAllByText('Forecasts').length).toBeGreaterThan(0);
  });

  it('reports the session limits the API returns', async () => {
    render(<AiGovernance />);
    await waitFor(() => expect(screen.getByText('Analyst')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('tab', { name: /Sessions/ }));

    await waitFor(() => expect(screen.getByText('Idle timeout')).toBeInTheDocument());
    expect(screen.getByText('30 min')).toBeInTheDocument();
    expect(screen.getByText('4 hrs')).toBeInTheDocument();
    expect(screen.getByText('40 turns')).toBeInTheDocument();
    expect(screen.getByText('6 /min')).toBeInTheDocument();
  });

  it('refuses to show allowances when the API did not return them', async () => {
    // Without the enforced list, every figure on the quotas tab would be
    // unverified — so none is shown.
    mockGet.mockImplementation(() => Promise.reject(new TypeError('offline')));
    render(<AiGovernance />);

    await waitFor(() => expect(screen.getByText('Allowances unavailable')).toBeInTheDocument());
    expect(screen.queryByText('Analyst')).toBeNull();
    expect(screen.queryByText('Data Clerk')).toBeNull();
  });

  it('does not substitute an invented budget when no seat can be priced', async () => {
    // Usage arrives but the tiers payload prices no seat, so the workspace budget
    // has no configured allowance. The panel must say that rather than fall back
    // to the retired hardcoded 6000 — a figure no limit configured on screen.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/admin/agent/tiers')
        return Promise.resolve({ data: [], fallback_tier: '', session: null });
      if (path === '/api/v1/admin/agent/usage')
        return Promise.resolve({
          data: [{ date: '2026-09-24', questions: 120, tool_calls: 240, refusals: 0 }],
        });
      return Promise.resolve({ data: [] });
    });
    render(<AiGovernance />);

    expect(
      await screen.findByText(/no allowance configured for this workspace/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/6,?000/)).toBeNull();
  });

  it('names each seat and charts the usage the quota API reports', async () => {
    // `GET /api/v1/admin/agent/quotas` returns the member's name and email, both
    // usage counters and the seat's own daily override. It used to be read as
    // bare user ids with no usage at all, so every seat showed as
    // "Seat 1a2b3c4d" with an empty Today and This month bar.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/admin/agent/tiers') return Promise.resolve(TIERS);
      if (path === '/api/v1/admin/agent/quotas')
        return Promise.resolve({
          data: [
            {
              user_id: 'u1',
              name: 'Aline Umutoni',
              email: 'clerk@demandiq.app',
              tier: 'operator',
              used_today: 12,
              used_month: 240,
              limit: 60,
              month_limit: 600,
              daily_override: 90,
              status: 'active',
            },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<AiGovernance />);

    await waitFor(() => expect(screen.getByText('Aline Umutoni')).toBeInTheDocument());
    expect(screen.getByText('clerk@demandiq.app')).toBeInTheDocument();
    // Today is measured against the seat's own override, which the row states.
    expect(screen.getByText('12/90')).toBeInTheDocument();
    expect(screen.getByText('240/600')).toBeInTheDocument();
    expect(screen.getByText('override')).toBeInTheDocument();
  });

  it('never claims a device, a turn count or an idle time it was not given', async () => {
    // A session payload has no device at all, and states a turn count or a
    // last-seen only when it knows them. The list used to print "0 turns" and a
    // literal "idle —" beside a "— ·" device slot for every session.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/admin/agent/tiers') return Promise.resolve(TIERS);
      if (path === '/api/v1/admin/agent/sessions')
        return Promise.resolve({
          data: [
            {
              id: 's1',
              user_id: 'u1',
              user_name: 'Aline Umutoni',
              started_at: '2026-09-25T09:00:00Z',
              last_seen_at: '2026-09-25T09:10:00Z',
              location: 'Remera Grill',
              turn_count: 4,
            },
            {
              id: 's2',
              user_id: 'u2',
              user_name: 'Jean Bosco',
              started_at: '2026-09-25T09:05:00Z',
              last_seen_at: null,
              location: null,
              turn_count: null,
            },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<AiGovernance />);
    await userEvent.click(await screen.findByRole('tab', { name: /Sessions/ }));

    await waitFor(() => expect(screen.getByText(/Aline Umutoni/)).toBeInTheDocument());
    expect(screen.getByText('4 turns')).toBeInTheDocument();
    expect(screen.getByText('Remera Grill', { exact: false })).toBeInTheDocument();
    expect(
      screen.getByText(/idle (just now|\d+ min ago|\d+ h ago|\d+ d ago)/),
    ).toBeInTheDocument();

    // The second session states neither, so neither is printed for it.
    expect(screen.getByText(/Jean Bosco/)).toBeInTheDocument();
    // Exactly "0 turns" — the session-limits panel legitimately reports 40.
    expect(screen.queryByText('0 turns')).toBeNull();
    expect(screen.getAllByText(/^idle /)).toHaveLength(1);
  });

  it('describes audit activity in words, never as a request line', async () => {
    // The audit log's `action` is the literal request line and its `target` is a
    // path; neither may reach an operator. Both go through the shared helpers.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/admin/agent/tiers') return Promise.resolve(TIERS);
      if (path === '/api/v1/admin/agent/usage')
        return Promise.resolve({
          data: [{ date: '2026-09-24', questions: 3, tool_calls: 6, refusals: 0 }],
        });
      if (path === '/api/v1/audit-log')
        return Promise.resolve({
          data: [
            {
              id: 'a1',
              action: 'POST /api/v1/agent/messages',
              target: '/api/v1/agent/messages',
              created_at: '2026-09-24T10:00:00Z',
            },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<AiGovernance />);

    await userEvent.click(await screen.findByRole('tab', { name: /Guardrails/ }));
    await waitFor(() => expect(screen.getByText(/Question asked in the assistant/)).toBeInTheDocument());
    expect(screen.queryByText(/POST \/api\/v1\/agent\/messages/)).toBeNull();
  });
});
