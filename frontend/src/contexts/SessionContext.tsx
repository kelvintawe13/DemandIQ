import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, setToken, setOn401, ApiException } from '../api/client';
import { onSessionExpired, SESSION_EXPIRED_MESSAGE } from '../lib/sessionExpiry';
import { useToast } from './ToastContext';

export interface SessionUser {
  name: string;
  role: string;
  /**
   * The backend's role value (``owner``/``administrator``/``analyst``/
   * ``operator``/``viewer``). ``role`` is the display label; screens that need
   * to *compare* roles — member management, which may only act on accounts
   * below the actor — must rank this instead of parsing a label.
   */
  roleKey: string;
  email: string;
  initials: string;
  id?: string;
  workspaceId?: string;
  permissions?: string[];
  /** Real MFA enrolment from ``/auth/me`` — never inferred from the role. */
  mfaEnabled?: boolean;
  phone?: string | null;
  job_title?: string | null;
  jobTitle?: string | null;
  avatar_url?: string | null;
  avatarUrl?: string | null;
  created_at?: string | null;
  createdAt?: string | null;
  bio?: string | null;
  /** Platform-level administrator (manages all workspaces & site settings). */
  isSiteAdmin?: boolean;
  /**
   * Whether a password is set. False for OAuth-only accounts, which cannot
   * confirm an account erasure with one. Defaults to true so a missing field
   * in an older cached response fails safe (asks for a password) rather than
   * silently skipping the confirmation step.
   */
  hasPassword?: boolean;
}

interface SessionContextValue {
  user: SessionUser;
  signOut: () => void;
  /** React's setter, so callers may pass a value or a `prev => next` updater. */
  setUser: React.Dispatch<React.SetStateAction<SessionUser>>;
  /** Rehydrate from `/auth/me`. Returns the mapped user on success, else null. */
  refresh: () => Promise<SessionUser | null>;
  loading: boolean;
  error: string | null;
  isAuthenticated: boolean;
  /** True while a fresh OAuth account still owes profile completion. */
  profileGatePending: boolean;
  /** Clear the gate after successful profile completion. */
  clearProfileGate: () => void;
}

function bool(v: unknown): boolean {
  return v === true || v === 'true';
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '??';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function normalizeRole(raw: string): string {
  // Mirrors the backend roles (app/models/user.py). Owner and administrator are
  // separate roles: the owner runs the business, the administrator runs the
  // platform and the models. The backend's `operator` is shown as Data Clerk.
  const low = raw.toLowerCase();
  if (low === 'owner') return 'Owner';
  if (low === 'administrator') return 'Administrator';
  if (low === 'analyst') return 'Analyst';
  if (low === 'viewer') return 'Viewer';
  return 'Data Clerk';
}

interface MeResponse {
  user: {
    id: string;
    workspace_id: string;
    name: string;
    email: string;
    role: string;
    mfa_enabled: boolean;
    email_verified: boolean;
    profile_completed?: boolean;
    is_site_admin?: boolean;
  };
  workspace?: { id: string; name: string } | null;
  permissions: string[];
  /** False for OAuth-only accounts (Google) — no password to confirm. */
  has_password?: boolean;
}

export function mapMeToSessionUser(me: MeResponse): SessionUser {
  const u = me.user as any;
  return {
    name: u.name,
    email: u.email,
    role: normalizeRole(u.role),
    roleKey: String(u.role ?? '').toLowerCase(),
    initials: initialsFor(u.name),
    id: u.id,
    workspaceId: u.workspace_id,
    permissions: me.permissions,
    mfaEnabled: u.mfa_enabled === true,
    phone: u.phone ?? null,
    job_title: u.job_title ?? null,
    jobTitle: u.job_title ?? null,
    avatar_url: u.avatar_url ?? null,
    avatarUrl: u.avatar_url ?? null,
    created_at: u.created_at ?? null,
    createdAt: u.created_at ?? null,
    bio: (u as any).bio ?? (u as any).about ?? null,
    isSiteAdmin: bool(u.is_site_admin),
    hasPassword: me.has_password !== false,
  };
}

// Least privilege: a consumer rendered outside the provider gets read-only
// navigation rather than the modelling console. Mirrors the backend, where an
// unrecognised role falls back to the viewer bundle.
const defaultUser: SessionUser = {
  name: 'Viewer',
  role: 'Viewer',
  roleKey: 'viewer',
  email: 'demo@demandiq.app',
  initials: 'VW',
};

const SessionContext = createContext<SessionContextValue>({
  user: defaultUser,
  signOut: () => undefined,
  setUser: () => undefined,
  refresh: async () => null,
  loading: false,
  error: null,
  isAuthenticated: false,
  profileGatePending: false,
  clearProfileGate: () => undefined,
});

export function SessionProvider({
  children,
  user: externalUser,
  signOut: externalSignOut,
}: {
  children: React.ReactNode;
  user?: SessionUser;
  signOut?: () => void;
}) {
  const [user, setUserState] = useState<SessionUser>(externalUser ?? defaultUser);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Profile-completion gate: surfaced from /auth/me's profile_completed flag
  // so a gated user re-entering the app is re-routed to CompleteProfile.
  const [profileGatePending, setProfileGatePending] = useState(false);
  // Expiry toasts (provider order guarantee: ToastProvider wraps this one).
  const { toast } = useToast();

  // keep in sync when App passes a new user (demo fallback or real login).
  // Skip echo updates: App re-derives its seat from this context and passes
  // it back down, so without this guard the two states ping-pong new object
  // identities — every consumer re-renders and the header identity visibly
  // flickers on restore.
  useEffect(() => {
    if (!externalUser) return;
    setUserState((current) => {
      return current.email === externalUser.email && current.role === externalUser.role
        ? current
        : externalUser;
    });
  }, [externalUser]);

  const setUser = useCallback<React.Dispatch<React.SetStateAction<SessionUser>>>(
    (next) => {
      setUserState(next);
    },
    [],
  );

  const refresh = useCallback(async (): Promise<SessionUser | null> => {
    // do not attempt if no token
    let token: string | null = null;
    try {
      token = localStorage.getItem('demandiq_token');
    } catch {
      token = null;
    }
    if (!token) return null;
    setLoading(true);
    setError(null);
    try {
      const me = await api.get<MeResponse>('/api/v1/auth/me');
      const mapped = mapMeToSessionUser(me);
      setUserState(mapped);
      setProfileGatePending(me.user.profile_completed === false);
      return mapped;
    } catch (e) {
      if (e instanceof ApiException && e.status === 401) {
        setToken(null);
        setError(e.error.message);
      } else if (e instanceof ApiException) {
        setError(e.error.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message);
      } else {
        // network/offline — keep current user, surface offline hint via error
        setError(null);
      }
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // on mount, hydrate from /auth/me if token exists
    void refresh();
  }, [refresh]);

  const signOut = useCallback(() => {
    try {
      setToken(null);
      // also try to revoke server session if refresh token exists (best-effort)
      const rt = localStorage.getItem('demandiq_refresh_token');
      if (rt) {
        void api.post('/api/v1/auth/logout', { refresh_token: rt }).catch(() => undefined);
        try {
          localStorage.removeItem('demandiq_refresh_token');
        } catch {}
      }
    } catch {}
    setUserState(defaultUser);
    setError(null);
    if (externalSignOut) externalSignOut();
  }, [externalSignOut]);

  // API client → session bridge: when a 401 cannot be recovered by the
  // silent refresh, tear the session down instead of silently logging out
  // later on a navigation that reads a null token.
  useEffect(() => {
    setOn401(() => signOut());
    return () => setOn401(null);
  }, [signOut]);

  // Session-expiry UX: on an unrecoverable 401 the API client fires the
  // expiry event; show the Bootstrap-style toast here — guarded to fire only
  // while a user session is actually active (not on the landing screen).
  useEffect(
    () =>
      onSessionExpired(() => {
        if (user.email) toast(SESSION_EXPIRED_MESSAGE, 'error');
      }),
    [toast, user.email],
  );

  const isAuthenticated = useMemo(() => {
    // The token is written before the seat lands, so a mount-time read alone
    // would report false. Referencing `user` makes the re-derive dependency
    // explicit (and satisfies react-hooks/exhaustive-deps, which cannot see
    // that the dep is load-bearing precisely because the body ignores it).
    void user;
    try {
      return Boolean(localStorage.getItem('demandiq_token'));
    } catch {
      return false;
    }
  }, [user]);

  const value = useMemo(
    () => ({
      user,
      signOut,
      setUser,
      refresh,
      loading,
      error,
      isAuthenticated,
      profileGatePending,
      clearProfileGate: () => setProfileGatePending(false),
    }),
    [user, signOut, setUser, refresh, loading, error, isAuthenticated, profileGatePending],
  );

  // TEMP DEBUG: expose live session state for browser probes.
  (window as any).__sessionDump = { email: user.email, role: user.role, isSiteAdmin: user.isSiteAdmin };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  return useContext(SessionContext);
}