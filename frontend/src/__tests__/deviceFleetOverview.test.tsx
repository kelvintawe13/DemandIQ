/**
 * The fleet console's overview: what the page counts, and which devices it says
 * want a person.
 *
 * The previous screen listed devices and nothing else, so a till that had never
 * checked in — the one state that stops a site feeding a forecast — looked
 * exactly as healthy as one reporting every minute: `status` is `active` from
 * the moment it is enrolled. These tests pin the reading that fixes that, and
 * the rules behind it.
 *
 * The device fixtures are dated **relative to now** on purpose. A hardcoded
 * `last_seen_at` would move in and out of the "quiet for a day" threshold as the
 * clock advanced, and a test that fails next Tuesday is worse than no test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      id: 'u1',
      name: 'Olivia Owner',
      role: 'Owner',
      roleKey: 'owner',
      permissions: ['devices:read', 'devices:manage', 'device_grants:manage'],
    },
  }),
}));
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {},
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { DeviceFleet } from '../pages/DeviceFleet';
import {
  STALE_AFTER_HOURS,
  attentionFor,
  appVersionCounts,
  devicesByLocation,
  fleetAttention,
  lastSeenBuckets,
  mapDevices,
  statusMixOf,
  type FleetDevice,
} from '../lib/deviceFleet';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

const NOW = Date.now();
const ago = (hours: number) => new Date(NOW - hours * 3_600_000).toISOString();

/** One row shaped like `GET /api/v1/devices`. */
function device(overrides: Record<string, unknown>) {
  return {
    id: 'd1',
    label: 'Till 2',
    device_type: 'pos',
    platform: 'Android 13',
    app_version: '2.4.0',
    location_id: 'loc1',
    credential_prefix: 'dq_a1b2',
    scopes: ['sales:write'],
    status: 'active',
    last_seen_at: ago(2),
    enrolled_at: ago(500),
    enrolled_by: 'u1',
    revoked_at: null,
    mirroring_consent_at: null,
    ...overrides,
  };
}

const FLEET = {
  data: [
    device({ id: 'd1', label: 'Till 2' }),
    device({ id: 'd2', label: 'KDS pass', device_type: 'kds', last_seen_at: null }),
    device({ id: 'd3', label: 'Lost till', status: 'revoked', last_seen_at: ago(12 * 24), revoked_at: ago(300) }),
    device({ id: 'd4', label: 'Back office', location_id: null, app_version: null, last_seen_at: ago(30) }),
  ],
};

const LOCATIONS = {
  data: [
    { id: 'loc1', name: 'Kigali, Nyamirambo' },
    { id: 'loc2', name: 'Kigali, Remera' },
  ],
};

beforeEach(() => {
  mockGet.mockReset();
  toast.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/devices') return Promise.resolve(FLEET);
    if (path === '/api/v1/locations') return Promise.resolve(LOCATIONS);
    return Promise.resolve({});
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

describe('the rules behind the fleet overview', () => {
  const rows: FleetDevice[] = mapDevices(FLEET, NOW);

  it('reads the heartbeat twice: as a sentence and as the timestamp behind it', () => {
    const [till, kds] = rows;
    // 2 hours ago lands in the minute-resolution sentence.
    expect(till.lastSeen).toBe('2 hours ago');
    expect(till.lastSeenAt).toBeTypeOf('number');
    // And the two agree about not knowing: "Never seen" must mean null, or a
    // device could print "Never seen" and still be counted in a bucket.
    expect(kds.lastSeen).toBe('Never seen');
    expect(kds.lastSeenAt).toBeNull();
  });

  it('treats an unparseable timestamp as no timestamp at all', () => {
    const [row] = mapDevices({ data: [device({ last_seen_at: 'not a date' })] }, NOW);
    expect(row.lastSeen).toBe('Never seen');
    expect(row.lastSeenAt).toBeNull();
  });

  it('lists a device that has never checked in, and one that has gone quiet', () => {
    expect(attentionFor(rows[0], NOW)).toBeNull();
    expect(attentionFor(rows[1], NOW)?.reason).toBe('never-seen');

    const quiet = mapDevices({ data: [device({ last_seen_at: ago(STALE_AFTER_HOURS + 1) })] }, NOW)[0];
    expect(attentionFor(quiet, NOW)?.reason).toBe('quiet');
    // The reason carries the reading, not just a verdict.
    expect(attentionFor(quiet, NOW)?.detail).toBe(`Has not checked in for ${quiet.lastSeen}.`);

    // One hour short of the threshold is not yet a problem, and the boundary is
    // the constant, not a number typed into the page.
    const almost = mapDevices({ data: [device({ last_seen_at: ago(STALE_AFTER_HOURS - 1) })] }, NOW)[0];
    expect(attentionFor(almost, NOW)).toBeNull();
  });

  it('leaves a revoked device off the attention list, because that decision is done', () => {
    const revoked = rows.find((row) => row.status === 'revoked') as FleetDevice;
    expect(attentionFor(revoked, NOW)).toBeNull();
  });

  it('lists a quarantined device whatever its heartbeat says', () => {
    const paused = mapDevices({ data: [device({ status: 'quarantined' })] }, NOW)[0];
    expect(attentionFor(paused, NOW)?.reason).toBe('quarantined');
  });

  it('orders the attention list by what a person should pick up first', () => {
    const list = fleetAttention(rows, NOW);
    expect(list.map((item) => item.reason)).toEqual(['never-seen', 'quiet']);
    expect(list.map((item) => item.device.label)).toEqual(['KDS pass', 'Back office']);
  });

  it('buckets every device exactly once, with "never" as its own state', () => {
    const buckets = lastSeenBuckets(rows, NOW);
    expect(buckets.map((bucket) => bucket.label)).toEqual([
      'Never',
      '< 1 hour',
      '1–24 hours',
      '1–7 days',
      '> 7 days',
    ]);
    // d1 two hours ago, d2 never, d3 revoked and last heard from twelve days
    // ago, d4 thirty hours ago.
    expect(buckets.map((bucket) => bucket.count)).toEqual([1, 0, 1, 1, 1]);
    expect(buckets.reduce((sum, bucket) => sum + bucket.count, 0)).toBe(rows.length);
  });

  it('splits the fleet by status and keeps an unknown app version as its own row', () => {
    expect(statusMixOf(rows)).toEqual({ active: 3, quarantined: 0, revoked: 1 });
    expect(appVersionCounts(rows)).toEqual([
      { label: '2.4.0', count: 3 },
      { label: 'Unknown version', count: 1 },
    ]);
    // A device bound to no restaurant is a bucket, not a dropped row: it feeds
    // no site's forecast, which is the kind of thing the panel exists to show.
    expect(devicesByLocation(rows, (id) => (id ? 'Kigali, Nyamirambo' : 'Not bound to a restaurant'))).toEqual([
      { label: 'Kigali, Nyamirambo', count: 3 },
      { label: 'Not bound to a restaurant', count: 1 },
    ]);
  });
});

describe('the fleet console overview', () => {
  it('leads with whether the fleet is reporting, not only what state it is in', async () => {
    render(<DeviceFleet />);

    expect(await tile('Devices')).toContain('4');
    expect(await tile('Devices')).toContain('3 of 4 bound to a restaurant');
    // One device checked in two hours ago; the other three have not.
    expect(await tile('Reporting')).toContain('1');
    expect(await tile('Reporting')).toContain(`Quiet for over ${STALE_AFTER_HOURS}h: 3`);
    expect(await tile('Needs attention')).toContain('2');
    expect(await tile('Needs attention')).toContain('1 never checked in');
    expect(await tile('Needs attention')).toContain('1 gone quiet');
    expect(await tile('Never checked in')).toContain('1');
  });

  it('names the devices that want a person, and why, beside the table', async () => {
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Needs attention' })).toBeInTheDocument());
    const panel = screen.getByRole('heading', { name: 'Needs attention' }).closest('section');
    expect(panel).not.toBeNull();
    const scoped = within(panel as HTMLElement);

    expect(scoped.getByText('KDS pass')).toBeInTheDocument();
    expect(scoped.getByText(/has never checked in/i)).toBeInTheDocument();
    expect(scoped.getByText('Back office')).toBeInTheDocument();
    // Revoked is a decision already taken, not a call to action.
    expect(scoped.queryByText('Lost till')).toBeNull();

    // The action opens the same dialog the table's row does.
    await userEvent.setup().click(scoped.getByRole('button', { name: 'Review KDS pass' }));
    await waitFor(() => expect(screen.getByText('What this device can do')).toBeInTheDocument());
  });

  it('says the fleet is fine when nothing wants a person', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') {
        return Promise.resolve({ data: [device({ label: 'Till 2', last_seen_at: ago(0.2) })] });
      }
      if (path === '/api/v1/locations') return Promise.resolve(LOCATIONS);
      return Promise.resolve({});
    });
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Needs attention' })).toBeInTheDocument());
    const panel = screen.getByRole('heading', { name: 'Needs attention' }).closest('section');
    expect(
      within(panel as HTMLElement).getByText(/every enrolled device has reported in/i),
    ).toBeInTheDocument();
    expect(await tile('Needs attention')).toContain('Nothing wants a person right now');
  });

  it('describes each chart from the same list the table renders', async () => {
    render(<DeviceFleet />);

    // Four panels, each fed from `rows` — the API-filtered list above them, so a
    // chart cannot count a device the table does not show.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Fleet status' })).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'When devices last checked in' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Devices by restaurant' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'App versions in the fleet' })).toBeInTheDocument();

    // The version panel states the spread in words, so a one-version fleet is
    // not a chart nobody can read.
    expect(screen.getByText(/2 different versions are in service/)).toBeInTheDocument();

    // Both restaurant buckets are named, including the unbound one. The names
    // appear more than once (a bar's axis label, the table, the attention list),
    // which is the page agreeing with itself rather than a duplicate render.
    expect(screen.getAllByText('Kigali, Nyamirambo').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Not bound to a restaurant/).length).toBeGreaterThan(0);
  });

  it('marks a stale heartbeat in the row as well as in the list', async () => {
    render(<DeviceFleet />);

    const row = (await screen.findAllByRole('row')).find((candidate) =>
      candidate.textContent?.includes('Back office'),
    );
    expect(row).toBeDefined();
    expect(within(row as HTMLElement).getByText('1 day ago')).toBeInTheDocument();
  });

  it('shows no overview at all for a workspace with no devices', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/devices') return Promise.resolve({ data: [] });
      if (path === '/api/v1/locations') return Promise.resolve(LOCATIONS);
      return Promise.resolve({});
    });
    render(<DeviceFleet />);

    await waitFor(() => expect(screen.getByText('No devices enrolled yet')).toBeInTheDocument());
    // A KPI strip of zeroes and four empty charts is noise, not a picture.
    expect(screen.queryByRole('heading', { name: 'Fleet status' })).toBeNull();
    expect(screen.queryByText('Needs attention')).toBeNull();
  });
});
