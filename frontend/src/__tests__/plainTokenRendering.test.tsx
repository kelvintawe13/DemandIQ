/**
 * The raw-token sweep, pinned.
 *
 * The API speaks in machine tokens (`model_promotion`, `pending`, `production`)
 * and those values were reaching screens verbatim. This suite covers the two
 * places where the wording is *not* just capitalisation but a different word —
 * an approval is not a queued job, and a model run's lifecycle status is not its
 * role — so a regression changes the meaning, not only the case.
 *
 * The capitalisation-only wires (invoice/endpoint/seat status, mapping status,
 * the assistant seat label) share `plainStatus`, whose vocabulary is covered by
 * plainLanguage.test.ts.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: { id: 'me', name: 'Ada Admin', role: 'Administrator', roleKey: 'administrator' },
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
import { Approvals } from '../pages/admin/Approvals';
import { ModelRegistry } from '../pages/admin/ModelRegistry';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

/** Any snake_case token in rendered text is a token an operator was never shown a key for. */
const SNAKE_CASE = /\b[a-z][a-z0-9]*_[a-z0-9_]+\b/;

beforeEach(() => {
  mockGet.mockReset();
});

describe('Approvals wording', () => {
  // The ids are the real shape — `requested_by`/`decided_by` are UUIDs and the API
  // also resolves the names. The screen used to print eight characters of the id,
  // which renders as "Decided by c6f6e213" — indistinguishable from a name.
  const REQUESTER_ID = 'c6f6e213-c17a-44e2-8e33-eeab9d45ddcf';
  const DECIDER_ID = '8a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9';
  const REQUESTS = {
    data: [
      {
        id: 'cr1',
        title: 'Promote lightgbm-it30 to production',
        kind: 'model_promotion',
        status: 'pending',
        requested_by: REQUESTER_ID,
        requested_by_name: 'Ada Admin',
        created_at: '2026-09-20T10:00:00Z',
      },
      {
        id: 'cr2',
        title: 'Change the preparation policy',
        kind: 'policy_change',
        status: 'approved',
        requested_by: REQUESTER_ID,
        requested_by_name: 'Ada Admin',
        decided_by: DECIDER_ID,
        decided_by_name: 'Grace Clerk',
        decided_at: '2026-09-19T11:00:00Z',
        created_at: '2026-09-19T10:00:00Z',
      },
    ],
  };

  beforeEach(() => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/change-requests' ? Promise.resolve(REQUESTS) : Promise.resolve({ data: [] }),
    );
  });

  it('names the request kind in words, not as a snake_case token', async () => {
    render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Promote lightgbm-it30/)).toBeInTheDocument());
    // 'Model promotions' also appears in the panel's own description, so match
    // the request line specifically: the kind sits beside the requester.
    expect(screen.getByText(/Model promotion · Ada Admin/)).toBeInTheDocument();
    expect(screen.queryByText(/model_promotion/)).not.toBeInTheDocument();
  });

  it('says a pending request awaits a decision, not that it is "waiting to start"', async () => {
    // `plainStatus('pending')` is the *pipeline* word ("Waiting to start"); using
    // it here would describe a queued job instead of a request waiting on a person.
    render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Promote lightgbm-it30/)).toBeInTheDocument());
    expect(screen.getAllByText('Awaiting a decision').length).toBeGreaterThan(0);
    expect(screen.queryByText('pending')).not.toBeInTheDocument();
    expect(screen.queryByText(/Waiting to start/)).not.toBeInTheDocument();
  });

  it('leaves no snake_case token anywhere on the screen', async () => {
    const { container } = render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Promote lightgbm-it30/)).toBeInTheDocument());
    expect(container.textContent ?? '').not.toMatch(SNAKE_CASE);
  });

  // The tab defaults to Pending, so the decided request is on the other tab.
  const openDecided = async () => {
    const { default: userEvent } = await import('@testing-library/user-event');
    await userEvent.click(screen.getByRole('tab', { name: /Decided/ }));
  };

  it('names who decided, instead of an eight-character slice of their id', async () => {
    render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Promote lightgbm-it30/)).toBeInTheDocument());
    await openDecided();
    await waitFor(() => expect(screen.getByText(/Decided by Grace Clerk/)).toBeInTheDocument());
    // The regression: the fragment read like a person's name.
    expect(screen.queryByText(new RegExp(DECIDER_ID.slice(0, 8)))).toBeNull();
  });

  it('names the requester rather than showing their id', async () => {
    const { container } = render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Promote lightgbm-it30/)).toBeInTheDocument());
    expect(screen.getByText(/Model promotion · Ada Admin/)).toBeInTheDocument();
    expect(container.textContent ?? '').not.toContain(REQUESTER_ID.slice(0, 8));
  });

  it('labels a short id as a seat when no name could be resolved', async () => {
    // A name can be missing (the actor's row could not be read). The id is then
    // still shown, but labelled — never bare, which is what made it look like a name.
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/change-requests'
        ? Promise.resolve({
            data: [
              {
                id: 'cr3',
                title: 'Grants an extra allowance',
                kind: 'access_grant',
                status: 'pending',
                requested_by: REQUESTER_ID,
                created_at: '2026-09-20T10:00:00Z',
              },
            ],
          })
        : Promise.resolve({ data: [] }),
    );
    render(<Approvals />);
    await waitFor(() => expect(screen.getByText(/Grants an extra allowance/)).toBeInTheDocument());
    expect(screen.getByText(new RegExp(`Seat ${REQUESTER_ID.slice(0, 8)}`))).toBeInTheDocument();
    // `access_grant` has no entry in the label map; the fallback still reads as words.
    expect(screen.getByText(/Access grant/)).toBeInTheDocument();
  });
});

describe('Model Registry wording', () => {
  const RUNS = {
    data: [
      {
        id: '11111111-2222-3333-4444-555555555555',
        version: 'lightgbm-it30',
        algorithm: 'lightgbm',
        status: 'production',
        trained_at: '2026-09-01T10:00:00Z',
        evaluation: { role: 'champion', mae: 6.7, rmse: 11.3, rmsle: 0.493 },
      },
      {
        id: '99999999-2222-3333-4444-555555555555',
        version: 'catboost-it30',
        algorithm: 'catboost',
        status: 'candidate',
        trained_at: '2026-09-02T10:00:00Z',
        evaluation: { role: 'challenger', mae: 6.69, rmse: 11.37, rmsle: 0.494 },
      },
    ],
  };

  beforeEach(() => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/models') return Promise.resolve(RUNS);
      return Promise.resolve({ data: [] });
    });
  });

  /**
   * The runs table, reached from a known row.
   *
   * The node is fetched fresh on every call: the fetch resolving replaces the
   * table, so a captured reference goes detached mid-test. `closest` reads the
   * DOM directly, so this stays independent of how the responsive table is
   * exposed to the accessibility tree.
   */
  const tableWithRuns = async () => {
    // The version also appears in the detail panel, so take the first copy that
    // actually sits inside the runs table.
    const copies = await screen.findAllByText('lightgbm-it30');
    const table = copies.map((el) => el.closest('table')).find(Boolean);
    expect(table).toBeTruthy();
    return table as HTMLTableElement;
  };

  it('heads the lifecycle column "Status", not "Role"', async () => {
    render(<ModelRegistry />);
    // The column shows `candidate`/`production`; calling it "Role" promised the
    // champion/challenger role and delivered a lifecycle stage. (The evaluation's
    // own role is still labelled "Role", but in the detail panel, not this table.)
    const text = (await tableWithRuns()).textContent ?? '';
    expect(text).toContain('Status');
    expect(text).not.toContain('Role');
  });

  it('shows the lifecycle status as words', async () => {
    render(<ModelRegistry />);
    const text = (await tableWithRuns()).textContent ?? '';
    expect(text).toContain('Production');
    expect(text).toContain('Candidate');
    // The panel legitimately says "In production" about the live model, so that
    // phrase is removed before checking the raw enum values are gone.
    const cells = text.replace(/In production/g, '');
    expect(cells).not.toMatch(/\bproduction\b/);
    expect(cells).not.toMatch(/\bcandidate\b/);
  });
});
