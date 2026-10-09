import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// The console is a site-admin surface; the actor is the platform owner so every
// hierarchy rule has someone above and below it to act on.
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ user: { id: 'me', role: 'Owner', roleKey: 'owner', isSiteAdmin: true } }),
}));
// A stable spy: the reset flow reports its outcome through a toast.
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../contexts/SiteMetaContext', () => ({
  useSiteMeta: () => ({ meta: null, refresh: vi.fn(), applyLocal: vi.fn() }),
}));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
}));

import { api } from '../api/client';
import { SiteAdmin } from '../pages/admin/SiteAdmin';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPatch = api.patch as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const ISO = '2026-09-20T10:00:00Z';

const OVERVIEW = {
  users: { total: 9, active: 8 },
  workspaces: 2,
  locations: 5,
  forecasts: 12,
  models: 3,
  roles: { owner: 4, viewer: 1 },
  recent_users: [],
};

const HEALTH = {
  app: { name: 'DemandIQ API', version: '1.0.0', environment: 'development', debug: false },
  database: { status: 'ok', latency_ms: 2.4 },
  uptime_seconds: 90061, // 1d 1h
  migration_revision: 'abc123',
  sessions: { active: 4 },
  pipeline: { latest_forecast_at: ISO, latest_training_at: ISO },
  settings: { maintenance_mode: false, registration_open: true },
};

const USERS = {
  data: [
    {
      id: 'u1',
      name: 'Grace Clerk',
      email: 'clerk@example.com',
      role: 'operator',
      workspace_name: 'DemandIQ Demo',
      email_verified: true,
      deactivated_at: null,
      is_site_admin: false,
      created_at: ISO,
    },
    {
      id: 'u2',
      name: 'Olivia Owner',
      email: 'owner@example.com',
      role: 'owner',
      workspace_name: 'DemandIQ Demo',
      email_verified: true,
      deactivated_at: null,
      is_site_admin: false,
      created_at: ISO,
    },
    {
      id: 'u3',
      name: 'Platform Admin',
      email: 'admin@demandiq.app',
      role: 'administrator',
      workspace_name: 'DemandIQ HQ',
      email_verified: true,
      deactivated_at: null,
      is_site_admin: true,
      created_at: ISO,
    },
  ],
  total: 3,
  limit: 25,
  offset: 0,
  has_more: false,
};

const WORKSPACES = {
  data: [
    {
      id: 'w1',
      name: 'DemandIQ Demo',
      business_type: 'cafe',
      country: 'RW',
      timezone: 'Africa/Kigali',
      created_at: ISO,
      users: 5,
      locations: 2,
      forecasts: 12,
    },
  ],
};

const AUDIT = {
  data: [
    {
      id: 1,
      action: 'site.user.update',
      target: 'u1',
      actor_email: 'admin@demandiq.app',
      client: 'web',
      // The middleware records the outcome of the privileged request here, which
      // is what the row reports; `after` is the change the admin made.
      meta: { after: { role: 'viewer' }, status_code: 200 },
      created_at: ISO,
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
  has_more: false,
};

const SETTINGS = {
  data: {
    registration_open: { value: true, is_default: true, description: 'Allow new account sign-ups' },
    default_role: { value: 'viewer', is_default: true, description: 'Seat granted to new sign-ups' },
    announcement: { value: '', is_default: true, description: 'Banner text' },
    support_email: { value: 'support@demandiq.app', is_default: true, description: 'Shown on errors' },
    maintenance_mode: { value: false, is_default: true, description: 'Block writes' },
    feature_flag_x: { value: true, is_default: false, description: 'A custom key' },
  },
};

function routeGet(path: string) {
  if (path.includes('/overview')) return Promise.resolve(OVERVIEW);
  if (path.includes('/health')) return Promise.resolve(HEALTH);
  if (path.includes('/users')) return Promise.resolve(USERS);
  if (path.includes('/workspaces')) return Promise.resolve(WORKSPACES);
  if (path.includes('/settings')) return Promise.resolve(SETTINGS);
  if (path.includes('/audit')) return Promise.resolve(AUDIT);
  return Promise.resolve({});
}

beforeEach(() => {
  mockGet.mockReset();
  mockPatch.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation(routeGet);
  mockPatch.mockResolvedValue({});
  toast.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SiteAdmin console', () => {
  it('organises the console into five named sections', async () => {
    render(<SiteAdmin />);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Overview', 'Users', 'Workspaces', 'Settings', 'Audit']);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('overview reports live platform health, not just counters', async () => {
    render(<SiteAdmin />);
    await waitFor(() => expect(screen.getByText('abc123')).toBeInTheDocument());
    expect(screen.getByText('1d 1h')).toBeInTheDocument();
    expect(screen.getByText(/ok · 2.4 ms/)).toBeInTheDocument();
    expect(screen.getByText('9')).toBeInTheDocument(); // users total
    expect(mockGet).toHaveBeenCalledWith('/api/v1/admin/site/health');
  });

  it('locks rows above the actor and never offers ownership', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Users' }));

    // A clerk below the owner is editable, and never sees Owner as a choice.
    const clerk = await screen.findByLabelText('Role for Grace Clerk');
    expect(clerk).toBeEnabled();
    expect(within(clerk).queryByRole('option', { name: 'Owner' })).toBeNull();

    // The owner is at the actor's own rank; the platform admin is flag-protected.
    expect(screen.getByLabelText('Role for Olivia Owner')).toBeDisabled();
    expect(screen.getByLabelText('Role for Platform Admin')).toBeDisabled();
    expect(screen.getByText('Owner is at or above your role')).toBeInTheDocument();
    expect(screen.getByText(/managed outside the directory/i)).toBeInTheDocument();
  });

  it('sends search, role and status filters to the directory endpoint', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Users' }));
    await screen.findByLabelText('Search users');

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'deactivated');
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/admin/site/users',
        expect.objectContaining({ status: 'deactivated', limit: 25, offset: 0 }),
      ),
    );

    await user.type(screen.getByLabelText('Search users'), 'grace');
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/admin/site/users',
        expect.objectContaining({ q: 'grace' }),
      ),
    );
  });

  it('renames a workspace through the endpoint the page never wired up', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Workspaces' }));

    await user.click(await screen.findByTitle('Edit workspace'));
    const name = screen.getByLabelText('Name');
    await user.clear(name);
    await user.type(name, 'Demo HQ');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith(
        '/api/v1/admin/site/workspaces/w1',
        expect.objectContaining({ name: 'Demo HQ' }),
      ),
    );
  });

  it('audit rows expose their metadata and their actor', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Audit' }));

    // The raw `site.user.update` token is turned into words; it must not surface.
    await waitFor(() => expect(screen.getByText(/Site user update/)).toBeInTheDocument());
    expect(screen.queryByText('site.user.update')).toBeNull();
    expect(screen.getByText(/admin@demandiq.app/)).toBeInTheDocument();
    expect(screen.getByText(/after/)).toBeInTheDocument();

    // And the row says whether it was accepted and which surface sent it, in the
    // same words the personal feed uses — not the API's `web` token.
    expect(screen.getByText(/accepted/)).toBeInTheDocument();
    expect(screen.getByText(/the web app/)).toBeInTheDocument();
  });

  it('confirms a password reset only when the API says it happened', async () => {
    // `POST /admin/site/users/{id}/reset-password` answers `password_reset: true`
    // once the password is stored and every live session for that account has
    // been revoked. A 200 was reported as success regardless.
    const user = userEvent.setup();
    const resetBtn = () => screen.findByRole('button', { name: 'Set a new password for clerk@example.com' });
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Users' }));

    await user.click(await resetBtn());
    await user.type(screen.getByLabelText('New password (min 8 chars)'), 'a-Long-Enough-Passw0rd');
    mockPost.mockResolvedValueOnce({ id: 'u1', password_reset: true });
    await user.click(screen.getByRole('button', { name: 'Reset password' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.stringContaining('Their sessions were signed out'),
      ),
    );
  });

  it('reports a reset the API did not confirm as unconfirmed, not as done', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Users' }));

    await user.click(
      await screen.findByRole('button', { name: 'Set a new password for clerk@example.com' }),
    );
    await user.type(screen.getByLabelText('New password (min 8 chars)'), 'a-Long-Enough-Passw0rd');
    // A 200 whose body carries no confirmation flag.
    mockPost.mockResolvedValueOnce({ id: 'u1' });
    await user.click(screen.getByRole('button', { name: 'Reset password' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('was not confirmed'), 'error'),
    );
    expect(toast).not.toHaveBeenCalledWith(expect.stringContaining('Their sessions were signed out'));
  });

  it('only surfaces unknown settings in the advanced escape hatch', async () => {
    const user = userEvent.setup();
    render(<SiteAdmin />);
    await user.click(await screen.findByRole('tab', { name: 'Settings' }));

    await waitFor(() => expect(screen.getByText('Registration open')).toBeInTheDocument());
    // The banner trio is edited in the banner panel, so it must not be repeated.
    expect(screen.getByText(/1 custom key/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('announcement')).toBeNull();
  });
});
