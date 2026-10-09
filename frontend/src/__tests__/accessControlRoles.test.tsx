/**
 * Access Control — the role picker honours the same hierarchy the API enforces.
 *
 * The console must never offer a change the server would refuse: an account may
 * only be moved to a role below the actor's own, ownership is never assignable,
 * and rows at or above the actor (including the actor's own) are locked with a
 * reason rather than silently failing on click.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

const session = { user: { role: 'Owner', roleKey: 'owner', id: 'me', isSiteAdmin: false } };
vi.mock('../contexts/SessionContext', () => ({ useSession: () => session }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { AccessControl } from '../pages/admin/AccessControl';

const ROLES = [
  { role: 'owner', label: 'Owner', rank: 4, assignable: false, permissions: [] },
  { role: 'administrator', label: 'Administrator', rank: 3, assignable: true, permissions: [] },
  { role: 'analyst', label: 'Analyst', rank: 2, assignable: true, permissions: [] },
  { role: 'operator', label: 'Data Clerk', rank: 1, assignable: true, permissions: [] },
  { role: 'viewer', label: 'Viewer', rank: 0, assignable: true, permissions: [] },
];

// The roster `GET /api/v1/members` returns: id, name, email, role,
// email_verified and created_at. It carries no `status` and no last-active
// timestamp, so the table must not render either.
const MEMBERS = [
  { id: 'me', name: 'Demo Owner', email: 'demo@demandiq.app', role: 'owner', email_verified: true, created_at: '2026-01-04T09:00:00Z' },
  { id: 'm2', name: 'Site Admin', email: 'admin@demandiq.app', role: 'administrator', email_verified: true, created_at: '2026-02-11T09:00:00Z' },
  { id: 'm3', name: 'Aline Umutoni', email: 'clerk@demandiq.app', role: 'operator', email_verified: false, created_at: '2026-03-02T09:00:00Z' },
];

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  session.user = { role: 'Owner', roleKey: 'owner', id: 'me', isSiteAdmin: false };
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
    if (path.includes('/roles')) return Promise.resolve({ data: ROLES });
    if (path.includes('/audit-log'))
      return Promise.resolve({
        data: [
          {
            id: 'aud-1',
            // The real shape: `action` is the request line, `target` the same
            // path, and the outcome/surface live in `meta_data`/`client`.
            action: 'POST /api/v1/members/invite',
            target: '/api/v1/members/invite',
            client: 'mobile',
            meta_data: { method: 'POST', path: '/api/v1/members/invite', status_code: 403 },
            actor_email: 'demo@demandiq.app',
            created_at: '2026-09-20T08:00:00Z',
          },
        ],
        has_more: false,
      });
    return Promise.resolve({ data: [] });
  });
});

const roleSelectFor = (name: string) =>
  screen.getByLabelText(`Role for ${name}`) as HTMLSelectElement;

const optionsOf = (select: HTMLSelectElement) =>
  [...select.options].map((o) => o.value);

describe('Access Control audit tab', () => {
  it('reports the outcome and the client, never the request line', async () => {
    const user = userEvent.setup();
    render(<AccessControl />);
    await user.click(await screen.findByRole('tab', { name: 'Activity' }));

    // The action in words, then what happened to it: the `target` is the request
    // path, so it is dropped in favour of the stored status code and client — and
    // the surface is named the way the rest of the console names it.
    await waitFor(() => expect(screen.getByText('Member invited')).toBeInTheDocument());
    // Page-wide: no panel on this screen may print a request path.
    expect(screen.queryByText(/\/api\/v1\/members\/invite/)).toBeNull();

    // These three are the *row's* claims, so they are read off the timeline
    // itself. The surface is named in the chart legend too now (`a phone`), and
    // asking the whole page would only prove the words appear somewhere.
    const timeline = document.querySelector('ol') as HTMLElement;
    expect(timeline).not.toBeNull();
    expect(within(timeline).getByText(/rejected \(403\)/)).toBeInTheDocument();
    expect(within(timeline).getAllByText(/a phone/).length).toBeGreaterThan(0);
  });
});

describe('Access Control role picker', () => {
  it('labels roles the way the product does, not the raw enum', async () => {
    render(<AccessControl />);
    // `operator` is the backend value; the console calls it Data Clerk.
    const row = (await waitFor(() => roleSelectFor('Aline Umutoni'))).closest('tr');
    expect(row?.textContent).toContain('Data Clerk');
    expect(row?.textContent).not.toContain('operator');
  });

  it('offers only roles below the actor, and never ownership', async () => {
    render(<AccessControl />);
    const select = await waitFor(() => roleSelectFor('Aline Umutoni'));
    expect(optionsOf(select)).toEqual(['administrator', 'analyst', 'operator', 'viewer']);
    expect(optionsOf(select)).not.toContain('owner');
  });

  it('locks the actor’s own row', async () => {
    render(<AccessControl />);
    const select = await waitFor(() => roleSelectFor('Demo Owner'));
    expect(select).toBeDisabled();
    expect(screen.getByText('You cannot change your own role')).toBeInTheDocument();
  });

  it('locks the revoke button of a row it cannot manage', async () => {
    render(<AccessControl />);
    await waitFor(() => roleSelectFor('Demo Owner'));
    // The owner's own row is first in the roster.
    expect(screen.getAllByRole('button', { name: 'Revoke' })[0]).toBeDisabled();
    // ...while a data clerk below the actor stays revocable.
    const revokes = screen.getAllByRole('button', { name: 'Revoke' });
    expect(revokes[revokes.length - 1]).toBeEnabled();
  });

  it('shows a data clerk on the role they already hold', async () => {
    render(<AccessControl />);
    const select = await waitFor(() => roleSelectFor('Aline Umutoni'));
    expect(select).toBeEnabled();
    expect(select.value).toBe('operator');
  });

  it('an administrator is offered strictly fewer roles than an owner', async () => {
    session.user = { role: 'Administrator', roleKey: 'administrator', id: 'm2', isSiteAdmin: false };
    render(<AccessControl />);
    const select = await waitFor(() => roleSelectFor('Aline Umutoni'));
    expect(optionsOf(select)).toEqual(['analyst', 'operator', 'viewer']);
  });

  it('an administrator cannot touch an owner', async () => {
    session.user = { role: 'Administrator', roleKey: 'administrator', id: 'm2', isSiteAdmin: false };
    render(<AccessControl />);
    const select = await waitFor(() => roleSelectFor('Demo Owner'));
    expect(select).toBeDisabled();
    expect(screen.getByText('Owner is at or above your role')).toBeInTheDocument();
  });

  it('shows the roster the API sends, not a status it never sends', async () => {
    render(<AccessControl />);
    await waitFor(() => roleSelectFor('Aline Umutoni'));

    // The member row has no `status` and no `last_active`, so "Last active"
    // rendered a permanent "—" and "Status" a permanent "active" pill — a state
    // the API does not track. The table now shows what the roster does carry.
    expect(screen.queryByText('Last active')).toBeNull();
    expect(screen.queryByText('Status')).toBeNull();
    expect(screen.queryByText('active')).toBeNull();
    expect(screen.getByText('Joined')).toBeInTheDocument();
    // A clerk who has not accepted the invitation is not called active.
    expect(screen.getByText('invited')).toBeInTheDocument();
    expect(screen.getAllByText('verified')).toHaveLength(2);
  });
});
