/**
 * The access console's overview, and the two lies its own API cannot support.
 *
 * The page used to be three plain lists. It now leads with what the workspace
 * actually looks like — who holds what, how much of the platform each role can
 * reach, how much privileged activity there is and how much of it was refused —
 * and this file pins both the maths and the three defects the redesign had to
 * fix, because all three were controls that *said* something the API does not do:
 *
 * * `POST /members/invite` takes email, role and name. The dialog asked for a
 *   location scope with three hardcoded restaurant names and dropped the answer
 *   on the floor. The Members column says "All locations" for everybody, and the
 *   dialog now agrees with it.
 * * `GET /members` takes no cursor and returns the whole roster. The tab's
 *   Previous/Next buttons were wired to the *audit log's* cursor, so the roster
 *   had a pager that paged nothing while the 45-entry trail had none.
 * * The audit timeline read an `entry.tone` no endpoint sends, so its severity
 *   dot was always grey. It is driven by the stored status code now.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: { id: 'me', name: 'Demo Owner', role: 'Owner', roleKey: 'owner', isSiteAdmin: false },
  }),
}));
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {},
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { AccessControl } from '../pages/admin/AccessControl';
import {
  auditActionCounts,
  auditLines,
  auditSurfaceMix,
  capabilityCounts,
  capabilityGaps,
  groupedCapabilities,
  inviteMix,
  memberRoleCounts,
  rejectedCount,
  rolesInUse,
  type AuditEntry,
} from '../lib/accessControl';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockDelete = api.delete as unknown as ReturnType<typeof vi.fn>;

const ROLES = [
  { role: 'owner', label: 'Owner', rank: 4, assignable: false, permissions: ['a', 'b', 'c'] },
  { role: 'administrator', label: 'Administrator', rank: 3, assignable: true, permissions: ['a', 'b', 'c', 'd', 'e'] },
  { role: 'analyst', label: 'Analyst', rank: 2, assignable: true, permissions: ['a', 'b'] },
  { role: 'operator', label: 'Data Clerk', rank: 1, assignable: true, permissions: ['a'] },
  { role: 'viewer', label: 'Viewer', rank: 0, assignable: true, permissions: [] },
];

const GROUPS = [
  { group: 'View', capabilities: ['a', 'b'] },
  { group: 'Platform', capabilities: ['c', 'd', 'e'] },
];

const MEMBERS = [
  { id: 'me', name: 'Demo Owner', email: 'demo@demandiq.app', role: 'owner', email_verified: true },
  { id: 'm2', name: 'Site Admin', email: 'admin@demandiq.app', role: 'administrator', email_verified: true },
  { id: 'm3', name: 'Aline Umutoni', email: 'clerk@demandiq.app', role: 'operator', email_verified: false },
];

function auditRow(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: 1,
    // A real row from this workspace's trail, id and all.
    action: 'PATCH /api/v1/quality/rules/0eadab84-db10-4ea4-b2e9-431f5e586189',
    target: '/api/v1/quality/rules/0eadab84-db10-4ea4-b2e9-431f5e586189',
    client: 'web',
    actor_email: 'demo@demandiq.app',
    meta_data: { status_code: 200 },
    created_at: '2026-09-28T10:00:00Z',
    ...overrides,
  };
}

const AUDIT = {
  data: [
    auditRow({ id: 1 }),
    auditRow({ id: 2, client: 'mobile', meta_data: { status_code: 403 } }),
    auditRow({ id: 3, action: 'POST /api/v1/policies/simulate', target: '/api/v1/policies/simulate' }),
    auditRow({ id: 4, action: 'POST /api/v1/policies/simulate', target: '/api/v1/policies/simulate' }),
  ],
  has_more: false,
  next_cursor: null,
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
  toast.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
    if (path.includes('/roles')) return Promise.resolve({ data: ROLES, groups: GROUPS });
    if (path.includes('/audit-log')) return Promise.resolve(AUDIT);
    return Promise.resolve({ data: [] });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  mockGet.mockReset();
});

/** A KPI tile's text, found by its own label. */
async function tile(label: string): Promise<string> {
  const nodes = await screen.findAllByText(label);
  const el = nodes.find((node) => node.tagName === 'P') ?? nodes[0];
  return el.closest('div.flex.min-w-0.flex-col')?.textContent ?? '';
}

describe('the counts behind the access console', () => {
  it('groups the roster by role, most senior first', () => {
    expect(memberRoleCounts(MEMBERS, ROLES)).toEqual([
      { label: 'Owner', count: 1 },
      { label: 'Administrator', count: 1 },
      { label: 'Data Clerk', count: 1 },
    ]);
    // A role nobody holds is left out rather than drawn as an empty bar.
    expect(memberRoleCounts(MEMBERS, ROLES).map((r) => r.label)).not.toContain('Viewer');
  });

  it('splits accepted invitations from outstanding ones', () => {
    expect(inviteMix(MEMBERS)).toEqual({ verified: 2, invited: 1 });
    expect(rolesInUse(MEMBERS)).toEqual(['administrator', 'operator', 'owner']);
  });

  it('counts capabilities per role, which is not the rank order', () => {
    // The point of the chart: the administrator holds more than the owner.
    expect(capabilityCounts(ROLES)).toEqual([
      { label: 'Owner', count: 3 },
      { label: 'Administrator', count: 5 },
      { label: 'Analyst', count: 2 },
      { label: 'Data Clerk', count: 1 },
      { label: 'Viewer', count: 0 },
    ]);
  });

  it('reports where the two capability lists disagree', () => {
    // The live registry's shape: a capability the roles hold that no area lists,
    // and one listed capability no role holds (`device:sync` is device-scoped).
    const roles = [
      { role: 'owner', label: 'Owner', rank: 4, permissions: ['a', 'b', 'agent:configure'] },
      { role: 'viewer', label: 'Viewer', rank: 0, permissions: [] },
    ];
    const groups = [{ group: 'View', capabilities: ['a', 'b', 'device:sync'] }];
    expect(groupedCapabilities(groups)).toEqual(['a', 'b', 'device:sync']);
    expect(capabilityGaps(roles, groups)).toEqual({
      heldButUngrouped: ['agent:configure'],
      groupedButUnheld: ['device:sync'],
    });
    // A capability listed under two areas is still one capability.
    expect(groupedCapabilities([{ capabilities: ['a'] }, { capabilities: ['a', 'b'] }])).toEqual(['a', 'b']);
    // When the two lists agree there is nothing to explain.
    expect(capabilityGaps([{ permissions: ['a'] }], [{ capabilities: ['a'] }])).toEqual({
      heldButUngrouped: [],
      groupedButUnheld: [],
    });
  });

  it('counts refused requests from the stored status code, and surfaces from `client`', () => {
    const rows = AUDIT.data;
    expect(rejectedCount(rows)).toBe(1);
    expect(auditSurfaceMix(rows)).toEqual({ web: 3, mobile: 1, api: 0 });
  });

  it('groups actions by what was done, in words, keeping the tail', () => {
    const counts = auditActionCounts(AUDIT.data);
    // Two simulations, one rule change, one policy comparison — and nothing that
    // reads like a request line.
    expect(counts.map((c) => c.label).join(' | ')).not.toMatch(/\/api\/v1/);
    expect(counts[0].count + counts.slice(1).reduce((sum, c) => sum + c.count, 0)).toBe(4);

    const long = Array.from({ length: 9 }, (_, i) =>
      auditRow({ id: 100 + i, action: `POST /api/v1/ingest/manual-${i}` }),
    );
    const capped = auditActionCounts(long, 3);
    expect(capped).toHaveLength(4);
    expect(capped[3].label).toMatch(/^Other \(/);
    expect(capped.reduce((sum, c) => sum + c.count, 0)).toBe(9);
  });

  it('renders a row with no request line, and flags the refused one', () => {
    const lines = auditLines(AUDIT.data);
    expect(lines[0].headline).not.toMatch(/\/api\/v1/);
    expect(lines[0].rejected).toBe(false);
    expect(lines[1].rejected).toBe(true);
    expect(lines[1].client).toBe('a phone');
    // The raw value stays beside the words, so the row can pick its own icon.
    expect(lines[1].clientKey).toBe('mobile');
    expect(lines[1].detail).toBe('rejected (403)');
  });
});

describe('the access console', () => {
  it('leads with the shape of the workspace, not the roster', async () => {
    render(<AccessControl />);

    expect(await tile('Members')).toContain('3');
    expect(await tile('Members')).toContain('2 with admin rights');
    expect(await tile('Awaiting invite')).toContain('1');
    expect(await tile('Awaiting invite')).toContain('has not signed in');
    expect(await tile('Privileged actions')).toContain('4');
    expect(await tile('Refused')).toContain('1');
  });

  it('says which window the privileged-action counts describe', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
      if (path.includes('/roles')) return Promise.resolve({ data: ROLES, groups: GROUPS });
      // A trail longer than the summary window: the counts are about the last
      // 100 events, and the page has to say so rather than imply a total.
      if (path.includes('/audit-log')) {
        return Promise.resolve({ data: [auditRow()], has_more: true, next_cursor: 'c1' });
      }
      return Promise.resolve({ data: [] });
    });
    render(<AccessControl />);

    expect(await tile('Privileged actions')).toContain('the last 100 privileged actions');
    expect(await tile('Refused')).toContain('Nothing refused in the last 100 privileged actions');
  });

  it('charts the roster and the invitations beside the table', async () => {
    render(<AccessControl />);

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Members by role' })).toBeInTheDocument());
    await waitFor(() => {
      const invites = screen
        .getByRole('heading', { name: 'Invitations' })
        .closest('section') as HTMLElement;
      // The legend states the counts without hovering, and does not reuse the
      // lowercase status word the table's pills carry.
      expect(within(invites).getByText('Verified')).toBeInTheDocument();
      expect(within(invites).getAllByText('2').length).toBeGreaterThan(0);
      expect(within(invites).getByText('Awaiting invite')).toBeInTheDocument();
    });
  });

  it('offers the roster no pager, because /members has no cursor', async () => {
    render(<AccessControl />);
    await screen.findByLabelText('Role for Aline Umutoni');

    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull();
    expect(mockGet).toHaveBeenCalledWith('/api/v1/members');
  });

  it('does not ask for a location scope the invite endpoint cannot store', async () => {
    const user = userEvent.setup();
    mockPost.mockResolvedValue({});
    render(<AccessControl />);

    await user.click(await screen.findByRole('button', { name: /Invite member/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByLabelText('Location scope')).toBeNull();
    expect(within(dialog).getByText(/Every member sees every location/i)).toBeInTheDocument();
    // A real form, so Enter submits from any field.
    const form = dialog.querySelector('form');
    expect(form).not.toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Send invite' })).toHaveAttribute('form', form!.id);

    await user.type(within(dialog).getByLabelText('Full name'), 'Grace Uwase');
    await user.type(within(dialog).getByLabelText('Work email'), 'grace@restaurant.rw');
    await user.click(within(dialog).getByRole('button', { name: 'Send invite' }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/members/invite', {
        email: 'grace@restaurant.rw',
        name: 'Grace Uwase',
        role: 'operator',
      }),
    );
  });

  it('confirms a revocation in the app’s own dialog, not window.confirm', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm');
    mockDelete.mockResolvedValue({});
    render(<AccessControl />);

    const revokes = await screen.findAllByRole('button', { name: 'Revoke' });
    await user.click(revokes[revokes.length - 1]);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/loses access to this workspace immediately/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/past actions stay in the audit log/i)).toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(mockDelete).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Revoke access' }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/api/v1/members/m3'));
  });

  it('sends the audit filters to the API and pages the trail it can page', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
      if (path.includes('/roles')) return Promise.resolve({ data: ROLES, groups: GROUPS });
      return Promise.resolve(AUDIT);
    });
    render(<AccessControl />);

    await user.click(await screen.findByRole('tab', { name: 'Activity' }));
    // The row reads in words, and neither the path nor a fragment of the id
    // reaches the timeline — the fixture is one of the workspace's own actions.
    await waitFor(() => {
      const timeline = document.querySelector('ol') as HTMLElement;
      expect(timeline).not.toBeNull();
      expect(within(timeline).getAllByText('Quality rule switched').length).toBeGreaterThan(0);
      expect(timeline.textContent).not.toMatch(/\/api\/v1\//);
      expect(timeline.textContent).not.toMatch(/0eadab84/);
    });

    await user.selectOptions(await screen.findByLabelText('Filter by who acted'), 'm3');
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/audit-log', expect.objectContaining({ actor_id: 'm3' })),
    );

    await user.selectOptions(await screen.findByLabelText('Filter by surface'), 'api');
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/audit-log',
        expect.objectContaining({ client: 'api', actor_id: 'm3' }),
      ),
    );

    // The filters are the API's own, so a cleared filter is another read rather
    // than a client-side widening of the page in hand.
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/audit-log',
        expect.objectContaining({ actor_id: undefined, client: undefined }),
      ),
    );
  });

  it('moves through the trail with a real cursor', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string, params?: Record<string, unknown>) => {
      if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
      if (path.includes('/roles')) return Promise.resolve({ data: ROLES, groups: GROUPS });
      if (path.includes('/audit-log')) {
        return Promise.resolve(
          params?.cursor
            ? { data: [auditRow({ id: 9, action: 'POST /api/v1/forecasts' })], has_more: false, next_cursor: null }
            : { data: AUDIT.data, has_more: true, next_cursor: 'cursor-1' },
        );
      }
      return Promise.resolve({ data: [] });
    });
    render(<AccessControl />);

    await user.click(await screen.findByRole('tab', { name: 'Activity' }));
    await user.click(await screen.findByRole('button', { name: 'Older' }));

    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/audit-log', expect.objectContaining({ cursor: 'cursor-1' })),
    );
    // Back the way it came.
    await user.click(await screen.findByRole('button', { name: 'Newer' }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/api/v1/audit-log', expect.not.objectContaining({ cursor: 'cursor-1' })),
    );
  });

  it('summarises each role’s reach on the Roles tab', async () => {
    const user = userEvent.setup();
    render(<AccessControl />);

    await user.click(await screen.findByRole('tab', { name: 'Roles' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Capabilities per role' })).toBeInTheDocument());
    // The surprising reading the matrix never gave: more capabilities, lower rank.
    expect(screen.getByText(/Administrator holds the most capabilities \(5 of 5\)/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Capabilities by area' })).toBeInTheDocument();
    // Each role column states its share of the registry.
    const table = document.querySelector('table') as HTMLElement;
    expect(within(table).getByText('100%')).toBeInTheDocument();
    // These fixtures cover the same capabilities from both sources, so there is
    // no gap to explain and the note stays away.
    expect(screen.queryByText(/draw from two API lists/)).toBeNull();
  });

  it('names the gap when the two capability lists disagree', async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((path: string) => {
      if (path.includes('/members')) return Promise.resolve({ data: MEMBERS });
      if (path.includes('/roles')) {
        return Promise.resolve({
          data: [
            { role: 'owner', label: 'Owner', rank: 4, assignable: false, permissions: ['a', 'b', 'c', 'agent:configure'] },
            { role: 'viewer', label: 'Viewer', rank: 0, assignable: true, permissions: [] },
          ],
          groups: [
            { group: 'View', capabilities: ['a', 'b'] },
            { group: 'Devices', capabilities: ['c', 'device:sync'] },
          ],
        });
      }
      return Promise.resolve(AUDIT);
    });
    render(<AccessControl />);
    await user.click(await screen.findByRole('tab', { name: 'Roles' }));

    // The two charts get a sentence rather than two silent totals: both counts,
    // both directions of the gap, and the capabilities by name.
    await waitFor(() => {
      const note = screen.getByText(/draw from two API lists/);
      expect(note.textContent).toMatch(/the roles hold 4, the areas list 4/);
      expect(note.textContent).toMatch(/1 the roles hold is listed under no area \(agent:configure\)/);
      expect(note.textContent).toMatch(/1 listed capability is held by no role \(device:sync\)/);
    });
  });
});
