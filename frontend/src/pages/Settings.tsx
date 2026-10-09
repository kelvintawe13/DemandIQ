import React, { useRef, useState } from 'react';
import {
  UserIcon,
  Building2Icon,
  SlidersHorizontalIcon,
  BellIcon,
  ShieldCheckIcon,
  SunIcon,
  MoonIcon,
  CheckIcon,
  ChevronDownIcon } from
'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { GoogleButton } from '../components/auth/GoogleButton';
import { isGoogleConfigured } from '../lib/google';
import { Panel } from '../components/ui/Panel';
import { Toggle } from '../components/ui/Toggle';
import { StatusPill } from '../components/ui/StatusPill';
import { Modal, ModalButton } from '../components/ui/Modal';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { DeleteAccountDialog } from '../components/ui/DeleteAccountDialog';
import { Field as FormField, inputClass, inputErrorClass } from '../components/ui/Field';
import { AnimatedTitle } from '../components/ui/AnimatedTitle';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';
import { useLocale, languages } from '../contexts/LocaleContext';
import { useSession } from '../contexts/SessionContext';
import { useLegal } from '../contexts/LegalContext';
import { api, ApiException } from '../api/client';
import { EmptyState } from '../components/ui/EmptyState';
import { LoadingState } from '../components/ui/LoadingState';
import { PermissionNote } from '../components/ui/PermissionNote';
import { mapSessions, type DeviceSession } from '../lib/sessions';
import { FilterBar } from '../components/ui/FilterBar';
import { ViewSwitcher } from '../components/ui/ViewSwitcher';
import { Pagination } from '../components/ui/Pagination';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { useListState, type FilterDef } from '../hooks/useListState';
import { applyWorkspaceTimezone } from '../hooks/useWorkspaceTimezone';

interface SettingsProps {
  onNavigate?: (id: string) => void;
  /** Opens directly on a section, e.g. from the sidebar user menu. */
  initialTab?: string;
}

/**
 * When a provider was linked, as `/me/identities` reports it per row. The
 * panel used to say only *whether* a method was connected; the endpoint has
 * always sent `linked_at` beside it.
 */
function linkedAtFor(rows: unknown, provider: string): string | null {
  if (!Array.isArray(rows)) return null;
  const row = rows.find(
    (x: any) => String(x?.provider ?? x?.id ?? x).toLowerCase() === provider,
  );
  return typeof row?.linked_at === 'string' ? row.linked_at : null;
}

function linkedOnLabel(iso: string | null): string {
  if (!iso) return '';
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleDateString();
}

const tabs = [
{ id: 'profile', label: 'Profile', icon: UserIcon },
{ id: 'organization', label: 'Organization', icon: Building2Icon },
{ id: 'preferences', label: 'Preferences', icon: SlidersHorizontalIcon },
{ id: 'notifications', label: 'Notifications', icon: BellIcon },
{ id: 'security', label: 'Security', icon: ShieldCheckIcon }];



export function Settings({ initialTab }: SettingsProps) {
  const [tab, setTab] = useState(initialTab ?? 'profile');
  const { theme, setTheme, colorScheme, setColorScheme, customColor, setCustomColor } = useTheme();
  const { locale, setLocale, t } = useLocale();
  const { user, signOut } = useSession();
  const { openLegal, reviewConsent, consent } = useLegal();

  const [name, setName] = useState(user.name);
  const [email, setEmail] = useState(user.email);
  const [density, setDensity] = useState<'comfortable' | 'compact'>('comfortable');
  const [emailDigest, setEmailDigest] = useState(true);
  const [pushAlerts, setPushAlerts] = useState(true);
  const [weeklyReport, setWeeklyReport] = useState(false);
  // MFA state comes from the account (``/auth/me`` -> ``mfa_enabled``).
  // It used to be inferred from the role, which showed two-factor as "On"
  // for an administrator who had never enrolled.
  const [twoFactor, setTwoFactor] = useState(Boolean(user.mfaEnabled));
  const [dialog, setDialog] = useState<
    'photo' |
    'password' |
    'mfa-setup' |
    'recovery' |
    'disconnect' |
    'revoke' |
    'delete-account' |
    undefined>(
  );
  const [googleLinked, setGoogleLinked] = useState<boolean | null>(null);
  const [googleLinkedAt, setGoogleLinkedAt] = useState<string | null>(null);
  const [identitiesError, setIdentitiesError] = useState<string | null>(null);
  const [identitiesLoading, setIdentitiesLoading] = useState(true);
  const [revokingId, setRevokingId] = useState<string>();
  const [sessions, setSessions] = useState<DeviceSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<{ variant: 'error'|'offline'; message: string } | null>(null);
  // The active-sessions list gets the same search/filter/views treatment as the
  // Devices screen — same rows, same badges, three ways to read them.
  const sessionFilters: FilterDef<DeviceSession>[] = [
    {
      key: 'trust',
      label: 'Show',
      match: (session, value) =>
        value === 'this' ? session.current : value === 'trusted' ? session.trusted && !session.current : !session.current,
      options: [
        { value: '', label: 'Every session' },
        { value: 'this', label: 'This device' },
        { value: 'trusted', label: 'Remembered devices' },
        { value: 'other', label: 'Other devices' },
      ],
    },
  ];
  const sessionList = useListState<DeviceSession>({
    key: 'business-settings-sessions',
    items: sessions,
    filters: sessionFilters,
    searchFields: (session) => [session.device, session.origin, session.time].filter(Boolean).join(' '),
    defaultView: 'list',
    pageSize: 12,
  });
  const sessionColumns: Column<DeviceSession>[] = [
    {
      key: 'device',
      label: 'Device',
      primary: true,
      render: (session) => (
        <span className="min-w-0">
          <span className="block truncate text-meta font-semibold text-slate-900">{session.device}</span>
          <span className="block text-micro text-slate-500">{session.origin ? `Signed in from ${session.origin}` : ''}</span>
        </span>
      ),
    },
    {
      key: 'trust',
      label: 'Trust',
      render: (session) => (
        <span className="flex flex-wrap items-center gap-1.5">
          {session.trusted && <StatusPill tone="success">Trusted device</StatusPill>}
          {session.current ? <StatusPill tone="success">This device</StatusPill> : session.revocable ? null : <StatusPill tone="neutral">Not revocable</StatusPill>}
        </span>
      ),
    },
    {
      key: 'actions',
      label: '',
      cellClassName: 'pr-0',
      render: (session) =>
        !session.current && session.revocable ? (
          <button
            type="button"
            onClick={() => {
              setRevokingId(session.id);
              setDialog('revoke');
            }}
            className="rounded-lg font-semibold text-red-600 transition-colors duration-200 ease-out hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500">
            Revoke
          </button>
        ) : null,
    },
  ];
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const { toast } = useToast();
  const close = () => setDialog(undefined);
  // Profile wiring
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>({});
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileApiError, setProfileApiError] = useState<string | null>(null);
  const [profileApiRef, setProfileApiRef] = useState<string | null>(null);
  const [phone, setPhone] = useState(user.phone ?? '');
  const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const phoneRe = /^\+?[0-9\s-]{7,32}$/;
  // Preferences / notifications fetch
  const [prefError, setPrefError] = useState<string | null>(null);
  // Where the app should open by default. `GET/PATCH /me/preferences` has always
  // carried `default_location`; no control ever set it.
  const [defaultLocation, setDefaultLocation] = useState<string>('');
  const [prefLocations, setPrefLocations] = useState<Array<{ id: string; name: string }>>([]);
  const [notifError, setNotifError] = useState<string | null>(null);
  // Workspace profile (Organization tab) — read by every seat, edited by the
  // owner/administrator. `can_manage` is the same capability the API enforces.
  const [workspace, setWorkspace] = useState<any>(null);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsSaving, setWsSaving] = useState(false);
  const [wsDraft, setWsDraft] = useState({ name: '', business_type: '', country: '', timezone: '' });
  // Password modal state
  const [pwCurrent, setPwCurrent] = useState('');
  const [pwNew, setPwNew] = useState('');
  const [pwConfirm, setPwConfirm] = useState('');
  const [pwFieldErrors, setPwFieldErrors] = useState<Record<string, string>>({});
  const [pwApiError, setPwApiError] = useState<string | null>(null);
  const [pwApiRef, setPwApiRef] = useState<string | null>(null);
  const [pwSaving, setPwSaving] = useState(false);
  // MFA enroll/confirm wiring
  const [mfaCode, setMfaCode] = useState('');
  const [mfaEnroll, setMfaEnroll] = useState<{ secret: string; provisioning_uri: string; recovery_codes?: string[] } | null>(null);
  const [mfaRecoveryCodes, setMfaRecoveryCodes] = useState<string[] | null>(null);
  const [mfaError, setMfaError] = useState<string | undefined>();
  const [mfaFieldError, setMfaFieldError] = useState<string | undefined>();
  const [mfaLoading, setMfaLoading] = useState(false);
  const [mfaReference, setMfaReference] = useState<string | undefined>();

  const handleMfaEnroll = async () => {
    setMfaError(undefined);
    setMfaFieldError(undefined);
    setMfaReference(undefined);
    setMfaLoading(true);
    try {
      const data = await api.post<{ secret: string; provisioning_uri: string; recovery_codes?: string[] }>('/api/v1/me/mfa/enroll');
      setMfaEnroll(data as any);
      toast('MFA enrollment started. Scan the QR code.', 'info');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setMfaError(msg);
        setMfaReference(e.error.reference ?? undefined);
        toast(msg, 'error');
      } else {
        const msg = e instanceof Error ? e.message : 'Enrollment failed';
        setMfaError(msg);
        toast(msg, 'error');
      }
    } finally {
      setMfaLoading(false);
    }
  };

  const handleMfaConfirm = async () => {
    setMfaError(undefined);
    setMfaFieldError(undefined);
    setMfaReference(undefined);
    if (!/^\d{6}$/.test(mfaCode)) {
      setMfaFieldError('Enter a 6-digit code from your authenticator app.');
      return;
    }
    setMfaLoading(true);
    try {
      const data = await api.post<{ message: string; recovery_codes: string[] }>('/api/v1/me/mfa/confirm', { code: mfaCode });
      setTwoFactor(true);
      setMfaRecoveryCodes(data.recovery_codes);
      setMfaEnroll(null);
      setMfaCode('');
      toast('Two-factor enabled. Save your recovery codes.', 'success');
      // Stay on mfa-setup so the recovery-code grid below can render. Closing
      // here used to hide the only chance to copy the codes after enroll.
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setMfaError(msg);
        setMfaReference(e.error.reference ?? undefined);
        if (e.error.field === 'code') setMfaFieldError(e.error.message);
        toast(msg, 'error');
      } else {
        const msg = e instanceof Error ? e.message : 'Verification failed';
        setMfaError(msg);
        toast(msg, 'error');
      }
    } finally {
      setMfaLoading(false);
    }
  };

  const handleRegenerateRecovery = async () => {
    setMfaLoading(true);
    try {
      const data = await api.post<{ recovery_codes: string[] }>('/api/v1/me/mfa/recovery-codes');
      setMfaRecoveryCodes(data.recovery_codes);
      toast('New recovery codes generated. Store them safely.', 'success');
      // Show the new set in the MFA panel — closing here discarded them.
      setDialog('mfa-setup');
    } catch (e) {
      const msg = e instanceof ApiException ? e.error.message : e instanceof Error ? e.message : 'Failed to regenerate';
      toast(msg, 'error');
    } finally {
      setMfaLoading(false);
    }
  };

  // Preferences & notifications — parallel so the Preferences tab does not sit
  // on stale defaults while three serial round-trips finish.
  React.useEffect(() => {
    void (async () => {
      const [prefsResult, locsResult, notifResult] = await Promise.allSettled([
        api.get<{ theme?: string; language?: string; density?: string; default_location?: string | null }>('/api/v1/me/preferences'),
        api.get<{ data: Array<{ id: string; name: string }> }>('/api/v1/locations', { limit: 100 }),
        api.get<{ email_digest?: boolean; push_alerts?: boolean; weekly_report?: boolean }>('/api/v1/me/notifications'),
      ]);

      if (prefsResult.status === 'fulfilled') {
        const p = prefsResult.value;
        if (p.theme === 'light' || p.theme === 'dark' || p.theme === 'system') setTheme(p.theme as any);
        if (p.language) setLocale(p.language as any);
        if (p.density === 'comfortable' || p.density === 'compact') setDensity(p.density);
        setDefaultLocation(typeof p.default_location === 'string' ? p.default_location : '');
      } else if (prefsResult.reason instanceof ApiException) {
        setPrefError(prefsResult.reason.error.message);
      }

      if (locsResult.status === 'fulfilled') {
        setPrefLocations((locsResult.value.data ?? []).map((l) => ({ id: String(l.id), name: l.name })));
      } else {
        // The select falls back to "ask each time" rather than failing the tab.
        setPrefLocations([]);
      }

      if (notifResult.status === 'fulfilled') {
        const n = notifResult.value;
        if (typeof n.email_digest === 'boolean') setEmailDigest(n.email_digest);
        if (typeof n.push_alerts === 'boolean') setPushAlerts(n.push_alerts);
        if (typeof n.weekly_report === 'boolean') setWeeklyReport(n.weekly_report);
      } else if (notifResult.reason instanceof ApiException) {
        setNotifError(notifResult.reason.error.message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sessions & identities wiring
  React.useEffect(() => {
    void (async () => {
      setSessionsLoading(true); setSessionsError(null);
      try {
        const data: any = await api.get<any>('/api/v1/me/sessions');
        // Mapping shared with the Profile panel and the Devices screen
        // (`lib/sessions.ts`): `GET /api/v1/me/sessions` reports `device`,
        // `origin` (the sign-in's recorded origin, née `ip_country`),
        // `trusted_device`,
        // `last_seen_at` and which row the request's own token belongs to (the
        // `sid` claim). It sends no `created_at`, so the time column used to
        // print "—" for every row.
        setSessions(mapSessions(data));
      } catch (e) {
        if (e instanceof ApiException) {
          const isOffline = !navigator.onLine || e.status === 0;
          setSessionsError({ variant: isOffline ? 'offline' : 'error', message: e.error.message });
        } else {
          setSessionsError({ variant: !navigator.onLine ? 'offline' : 'error', message: e instanceof Error ? e.message : 'Failed to load' });
        }
      } finally { setSessionsLoading(false); }
    })();
    void (async () => {
      setIdentitiesLoading(true); setIdentitiesError(null);
      try {
        let data: any = null;
        try {
          data = await api.get<any>('/api/v1/me/identities');
        } catch (e) {
          if (e instanceof ApiException && (e.status === 404 || e.status === 501)) {
            const me: any = await api.get<any>('/api/v1/auth/me');
            data = me.identities ?? me.user?.identities ?? [];
            if (Array.isArray(data)) {
              // normalize to provider list
              const hasGoogle = data.some((x: any) => String(x.provider ?? x).toLowerCase() === 'google');
              setGoogleLinked(hasGoogle);
              setGoogleLinkedAt(linkedAtFor(data, 'google'));
              return;
            }
          } else throw e;
        }
        const list: any[] = Array.isArray(data) ? data : data?.data ?? data?.identities ?? [];
        const hasGoogle = list.some((x: any) => String(x.provider ?? x.id ?? x).toLowerCase() === 'google');
        setGoogleLinked(hasGoogle);
        setGoogleLinkedAt(linkedAtFor(list, 'google'));
      } catch (e) {
        if (e instanceof ApiException) setIdentitiesError(e.error.message);
        setGoogleLinked(false);
      } finally { setIdentitiesLoading(false); }
    })();
  }, []);

  // Workspace profile — fetched once, the first time the Organization tab opens.
  //
  // The guard is `wsAttempted`, NOT `wsLoading`. Keying it on the loading flag
  // meant a FAILED load re-triggered itself: the `finally` set loading back to
  // false, which changed the dependency array, so the effect fetched again, and
  // failed again — indefinitely. On any backend without `/workspace` (a rolled
  // back deploy, a stale dev server) the Organization tab flickered forever.
  // A failed load now settles on the error state, with Retry as the way out.
  const wsAttempted = useRef(false);

  const loadWorkspace = async () => {
    setWsLoading(true);
    setWsError(null);
    try {
      const ws = await api.get<any>('/api/v1/workspace');
      setWorkspace(ws);
      setWsDraft({
        name: ws.name ?? '',
        business_type: ws.business_type ?? '',
        country: ws.country ?? '',
        timezone: ws.timezone ?? '',
      });
    } catch (e) {
      setWsError(
        e instanceof ApiException
          ? e.error.message + (e.reference ? ` (ref: ${e.reference})` : '')
          : 'Failed to load the workspace profile',
      );
    } finally {
      setWsLoading(false);
    }
  };

  React.useEffect(() => {
    if (tab !== 'organization' || wsAttempted.current) return;
    wsAttempted.current = true;
    void loadWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const saveWorkspace = async () => {
    setWsSaving(true);
    setWsError(null);
    try {
      const updated = await api.patch<any>('/api/v1/workspace', {
        name: wsDraft.name.trim(),
        business_type: wsDraft.business_type.trim() || null,
        country: wsDraft.country.trim().toUpperCase() || null,
        timezone: wsDraft.timezone.trim() || null,
      });
      setWorkspace((current: any) => ({ ...current, ...updated, can_manage: true }));
      // Adopt the new zone immediately: every "today" on every screen is read in
      // it, so a saved timezone must not wait for a reload to take effect.
      applyWorkspaceTimezone(updated?.timezone ?? wsDraft.timezone);
      toast('Workspace profile saved.');
    } catch (e) {
      const msg =
        e instanceof ApiException
          ? e.error.message + (e.reference ? ` (ref: ${e.reference})` : '')
          : 'Failed to save the workspace profile';
      setWsError(msg);
      toast(msg, 'error');
    } finally {
      setWsSaving(false);
    }
  };

  const patchPreferences = async (patch: Record<string, unknown>) => {
    setPrefError(null);
    try {
      await api.patch('/api/v1/me/preferences', patch);
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setPrefError(msg);
        toast(msg, 'error');
      }
    }
  };

  const patchNotifications = async (patch: Record<string, unknown>) => {
    setNotifError(null);
    try {
      await api.patch('/api/v1/me/notifications', patch);
      toast('Notification settings updated.');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setNotifError(msg);
        toast(msg, 'error');
      }
    }
  };

  const handleProfileSave = async () => {
    const errs: Record<string,string> = {};
    const n = name.trim();
    if (!n) errs.name = 'Name is required';
    else if (n.length <1 || n.length>200) errs.name = 'Name must be 1–200 characters';
    if (!emailRe.test(email.trim())) errs.email = 'Invalid email address';
    if (phone.trim()) {
      if (phone.length>32) errs.phone = 'Phone must be ≤ 32 characters';
      else if (!phoneRe.test(phone.trim())) errs.phone = 'Invalid phone format';
    }
    if (Object.keys(errs).length) { setProfileErrors(errs); return; }
    setProfileSaving(true);
    setProfileErrors({});
    setProfileApiError(null);
    setProfileApiRef(null);
    try {
      const payload: Record<string,string> = { name: n, email: email.trim().toLowerCase() };
      if (phone.trim()) payload.phone = phone.trim();
      await api.patch('/api/v1/me/profile', payload);
      toast('Profile saved.');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        if (e.error.field) setProfileErrors({ [e.error.field]: e.error.message });
        setProfileApiError(msg);
        setProfileApiRef(e.reference ?? null);
        toast(msg, 'error');
      } else {
        setProfileApiError(e instanceof Error ? e.message : 'Failed to save');
      }
    } finally { setProfileSaving(false); }
  };

  const handlePasswordUpdate = async () => {
    const errs: Record<string,string> = {};
    if (!pwCurrent) errs.current_password = 'Current password is required';
    if (!pwNew || pwNew.length < 8) errs.new_password = 'New password must be at least 8 characters';
    else if (pwNew.length > 128) errs.new_password = 'New password must be ≤ 128 characters';
    if (pwNew !== pwConfirm) errs.confirm = 'Passwords do not match';
    if (Object.keys(errs).length) { setPwFieldErrors(errs); return; }
    setPwSaving(true);
    setPwFieldErrors({});
    setPwApiError(null);
    setPwApiRef(null);
    try {
      await api.post('/api/v1/me/password', { current_password: pwCurrent, new_password: pwNew });
      toast('Password updated.');
      setPwCurrent(''); setPwNew(''); setPwConfirm('');
      close();
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        if (e.error.field) setPwFieldErrors({ [e.error.field]: e.error.message });
        setPwApiError(msg);
        setPwApiRef(e.reference ?? null);
        toast(msg, 'error');
      } else {
        setPwApiError(e instanceof Error ? e.message : 'Failed to update password');
      }
    } finally { setPwSaving(false); }
  };

  const handleAvatarFile = (f: File | null) => {
    setAvatarError(null);
    if (!f) { setAvatarFile(null); return; }
    if (!f.type.startsWith('image/')) { setAvatarError('Only image files are allowed'); return; }
    if (f.size > 5 * 1024 * 1024) { setAvatarError('Max 5 MB'); return; }
    setAvatarFile(f);
  };
  const handleAvatarUpload = async () => {
    if (!avatarFile) { setAvatarError('Choose an image'); return; }
    setAvatarUploading(true); setAvatarError(null);
    try {
      const fd = new FormData();
      fd.append('file', avatarFile);
      await api.postForm('/api/v1/me/avatar', fd);
      toast('Photo updated.');
      setAvatarFile(null);
      close();
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setAvatarError(msg);
        toast(msg, 'error');
      } else {
        setAvatarError(e instanceof Error ? e.message : 'Upload failed');
      }
    } finally { setAvatarUploading(false); }
  };

  const handleRevokeSession = async () => {
    if (!revokingId) return;
    try {
      await api.delete(`/api/v1/me/sessions/${revokingId}`);
      setSessions((cur) => cur.filter((s) => s.id !== revokingId));
      toast('Session revoked.', 'info');
      close();
    } catch (e) {
      const msg = e instanceof ApiException ? e.error.message : e instanceof Error ? e.message : 'Failed to revoke';
      toast(msg, 'error');
    }
  };
  const handleDisconnectGoogle = async () => {
    try {        await api.delete('/api/v1/me/identities/google');
        setGoogleLinked(false);
        setGoogleLinkedAt(null);
      toast('Google sign-in disconnected.', 'info');
      close();
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        // check last credential error hint
        toast(msg, 'error');
        setIdentitiesError(msg);
      } else {
        toast(e instanceof Error ? e.message : 'Failed', 'error');
      }
    }
  };

  const handleDeleteAccount = async (currentPassword: string | null) => {
    await api.delete('/api/v1/me/account', {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirm: 'DELETE',
        current_password: currentPassword,
      }),
    });
    // The account and its sessions are gone server-side; drop the local
    // tokens and return to sign-in rather than leaving a dead bearer in place.
    signOut();
    toast('Your account has been deleted.', 'info');
  };

  return (
    <>
      <div className="mb-5">
        <AnimatedTitle
          as="h1"
          variant="words"
          className="text-h2 tracking-tight text-slate-900 sm:text-display-sm"
        >
          Settings
        </AnimatedTitle>
        <p className="animate-subtitle-in mt-1.5 text-body-xs text-slate-500">
          Profile, workspace, preferences, and security for this seat.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        {/* Section nav: dropdown on mobile/tablet, vertical tabs on desktop. */}
        <div className="lg:sticky lg:top-0 lg:h-fit">
          <label className="relative flex items-center lg:hidden">
            <span className="sr-only">Settings sections</span>
            <select
              aria-label="Settings sections"
              value={tab}
              onChange={(e) => setTab(e.target.value)}
              className={`${inputClass} appearance-none pr-9 font-semibold`}
            >
              {tabs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <ChevronDownIcon className="pointer-events-none absolute right-3 h-4 w-4 text-slate-500" strokeWidth={2} aria-hidden="true" />
          </label>

          <nav
            aria-label="Settings sections"
            className="hidden gap-1 rounded-2xl border border-slate-200 bg-white p-2 shadow-[0_1px_0_rgba(15,23,42,0.03)] lg:flex lg:flex-col">

            {tabs.map((item) => {
              const Icon = item.icon;
              const isActive = item.id === tab;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={isActive ? 'page' : undefined}
                  onClick={() => setTab(item.id)}
                  className={[
                  'flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-left text-body-xs transition-[background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                  isActive ?
                  'bg-brand-700 font-semibold text-white shadow-sm' :
                  'font-medium text-slate-600 hover:bg-slate-50'].
                  join(' ')}>

                  <Icon className="h-5 w-5 shrink-0" strokeWidth={1.8} />
                  {item.label}
                </button>);

            })}
          </nav>
        </div>

        <div className="min-w-0">
          {tab === 'profile' &&
          <Panel title="Profile" description="How you appear across the workspace.">
              <div className="flex flex-col gap-5 rounded-2xl border border-slate-200 bg-slate-50/50 p-4 sm:flex-row sm:items-start sm:gap-4">
                <div className="relative shrink-0">
                  <span className="flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-600 to-brand-800 font-display text-h3-lg text-white shadow-lg">
                    {user.initials}
                  </span>
                  <button
                  type="button"
                  onClick={() => setDialog('photo')}
                  aria-label="Change profile photo"
                  title="Change profile photo"
                  className="tap-expand absolute -bottom-2 -right-2 flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:border-slate-300 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0zM18.75 10.5h.008v.008h-.008V10.5z" />
                    </svg>
                  </button>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-h5 font-semibold text-slate-900">{name}</p>
                  <p className="mt-1 text-body-xs text-slate-500">{user.role}</p>
                  <p className="mt-1 truncate text-caption text-slate-500">{user.email}</p>
                </div>
              </div>

              {profileApiError && (
                <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                  <p className="text-body-xs font-medium text-red-700">{profileApiError}</p>
                  {profileApiRef && <p className="mt-1 text-fine text-red-600">Reference: {profileApiRef}</p>}
                </div>
              )}

              <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <FormField id="set-name" label="Full name" error={profileErrors.name} hint={profileErrors.name ? undefined : '1–200 characters'}>
                  <input
                    id="set-name"
                    value={name}
                    onChange={(e) => {
                      setName(e.target.value);
                      setProfileErrors((prev) => {
                        const next = { ...prev };
                        delete next.name;
                        return next;
                      });
                    }}
                    maxLength={200}
                    className={[inputClass, profileErrors.name ? inputErrorClass : ''].filter(Boolean).join(' ')}
                  />
                </FormField>
                <FormField id="set-email" label="Email" error={profileErrors.email} hint={profileErrors.email ? undefined : 'Used for report delivery and alerts'}>
                  <input
                    id="set-email"
                    type="email"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      setProfileErrors((prev) => {
                        const next = { ...prev };
                        delete next.email;
                        return next;
                      });
                    }}
                    className={[inputClass, profileErrors.email ? inputErrorClass : ''].filter(Boolean).join(' ')}
                  />
                </FormField>
                <FormField id="set-role" label="Role" hint="Contact an owner to change roles">
                  <input id="set-role" value={user.role} readOnly className={inputClass} />
                </FormField>
                <FormField id="set-phone" label="Phone" error={profileErrors.phone} hint={profileErrors.phone ? undefined : 'Optional · max 32 characters'}>
                  <input
                    id="set-phone"
                    type="tel"
                    value={phone}
                    onChange={(e) => {
                      setPhone(e.target.value);
                      setProfileErrors((prev) => {
                        const next = { ...prev };
                        delete next.phone;
                        return next;
                      });
                    }}
                    maxLength={32}
                    placeholder="+250 788 000 000"
                    className={[inputClass, profileErrors.phone ? inputErrorClass : ''].filter(Boolean).join(' ')}
                  />
                </FormField>
              </div>

              <div className="mt-6 flex justify-end gap-3 border-t border-slate-100 pt-6">
                <button
                type="button"
                onClick={() => {
                  setName(user.name);
                  setEmail(user.email);
                  setPhone(user.phone ?? '');
                  setProfileErrors({});
                  setProfileApiError(null);
                  toast('Changes discarded', 'info');
                }}
                className="rounded-xl border border-slate-300 px-5 py-2.5 text-body-xs font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">

                  Cancel
                </button>
                <button
                type="button"
                disabled={profileSaving}
                onClick={() => void handleProfileSave()}
                className="rounded-xl bg-brand-700 px-5 py-2.5 text-body-xs font-semibold text-white shadow-sm transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-brand-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">

                  {profileSaving ? 'Saving…' : 'Save changes'}
                </button>
              </div>
            </Panel>
          }

          {tab === 'organization' &&
          <Panel
            title="Organization"
            description="The workspace every member of this seat belongs to.">

              {wsLoading ?
            <LoadingState label="Loading workspace profile" size="sm" /> :
            !workspace ?
            <div className="flex flex-col gap-3">
                  <EmptyState
                variant={wsError ? 'error' : 'empty'}
                size="sm"
                title={wsError ? 'Could not load the workspace' : 'No workspace profile'}
                description={wsError ?? 'The workspace profile is unavailable for this account.'}
                action={{ label: 'Retry', onClick: () => void loadWorkspace() }} />
                </div> :
            <>
                  <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-slate-200 bg-slate-50/60 px-4 py-4">
                    <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-slate-200 bg-white">
                      <Building2Icon className="h-7 w-7 text-brand-600" strokeWidth={1.6} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-body font-semibold text-slate-900">{workspace.name}</p>
                      <p className="mt-0.5 text-meta text-slate-500">
                        {workspace.business_type ?? 'Business type not set'} · {workspace.country ?? '—'} · {workspace.timezone ?? '—'}
                      </p>
                    </div>
                    {workspace.created_at &&
                <p className="text-micro text-slate-500">
                        Workspace since {new Date(workspace.created_at).toLocaleDateString()}
                      </p>
                }
                  </div>

                  <dl className="mt-5 grid grid-cols-3 gap-3">
                    {([
                ['Members', workspace.counts?.users],
                ['Locations', workspace.counts?.locations],
                ['Forecasts', workspace.counts?.forecasts]] as [string, number][]).
                map(([label, value]) =>
                <div key={label} className="rounded-xl border border-slate-200 px-3 py-2.5">
                        <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                        <dd className="text-stat-sm font-bold tabular-nums text-slate-900">{value ?? '—'}</dd>
                      </div>
                )}
                  </dl>

                  <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <FormField id="ws-name" label="Workspace name">
                      <input
                        id="ws-name"
                        value={wsDraft.name}
                        readOnly={!workspace.can_manage}
                        onChange={(e) => setWsDraft((d) => ({ ...d, name: e.target.value }))}
                        className={inputClass}
                      />
                    </FormField>
                    <FormField id="ws-type" label="Business type">
                      <input
                        id="ws-type"
                        value={wsDraft.business_type}
                        readOnly={!workspace.can_manage}
                        placeholder="cafe, restaurant, canteen"
                        onChange={(e) => setWsDraft((d) => ({ ...d, business_type: e.target.value }))}
                        className={inputClass}
                      />
                    </FormField>
                    <FormField id="ws-country" label="Country" hint="ISO 3166-1 alpha-2">
                      <input
                        id="ws-country"
                        value={wsDraft.country}
                        readOnly={!workspace.can_manage}
                        maxLength={2}
                        placeholder="RW"
                        onChange={(e) => setWsDraft((d) => ({ ...d, country: e.target.value.toUpperCase() }))}
                        className={inputClass}
                      />
                    </FormField>
                    <FormField id="ws-timezone" label="Timezone">
                      <input
                        id="ws-timezone"
                        value={wsDraft.timezone}
                        readOnly={!workspace.can_manage}
                        placeholder="Africa/Kigali"
                        onChange={(e) => setWsDraft((d) => ({ ...d, timezone: e.target.value }))}
                        className={inputClass}
                      />
                    </FormField>
                  </div>

                  {wsError && workspace &&
              <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                      <p className="text-body-xs font-medium text-red-700">{wsError}</p>
                    </div>
              }

                  {workspace.can_manage ?
              <div className="mt-6 flex justify-end border-t border-slate-100 pt-6">
                      <button
                  type="button"
                  disabled={wsSaving || !wsDraft.name.trim()}
                  onClick={() => void saveWorkspace()}
                  className="rounded-xl bg-brand-700 px-5 py-2.5 text-body-xs font-semibold text-white shadow-sm transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-brand-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        {wsSaving ? 'Saving…' : 'Save workspace'}
                      </button>
                    </div> :
              <PermissionNote className="mt-6">
                      Your seat can read the workspace but not change it. Owners and administrators manage these settings.
                    </PermissionNote>
              }
                </>
            }
            </Panel>
          }

          {tab === 'preferences' &&
          <Panel
            title="Preferences"
            description="These controls change the app immediately.">

              {prefError && (
                <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                  <p className="text-body-xs font-medium text-red-700">{prefError}</p>
                </div>
              )}
              <div className="flex flex-col gap-6">
                <div className="space-y-3">
                  <h3 className="text-control font-semibold text-slate-900">
                    {t('header.theme')}
                  </h3>
                  <div className="grid grid-cols-2 gap-3 sm:max-w-[360px]">
                    {(['light', 'dark'] as const).map((option) => {
                    const isActive = theme === option;
                    const Icon = option === 'light' ? SunIcon : MoonIcon;
                    return (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={isActive}
                        onClick={() => { setTheme(option); void patchPreferences({ theme: option }); }}
                        className={[
                        'flex items-center justify-center gap-2.5 rounded-xl border px-4 py-3 text-body-xs font-semibold transition-[background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                        isActive ?
                        'border-brand-500 bg-brand-50 text-brand-800 shadow-sm' :
                        'border-slate-300 text-slate-600 hover:bg-slate-50'].
                        join(' ')}>
                          <Icon className="h-5 w-5" strokeWidth={1.9} />
                          {option === 'light' ? t('header.light') : t('header.dark')}
                        </button>);

                  })}
                  </div>
                  <p className="text-caption text-slate-500">Choose between light and dark appearance</p>
                </div>

                <div className="space-y-3">
                  <h3 className="text-control font-semibold text-slate-900">
                    {t('header.colorScheme')}
                  </h3>
                  <div className="grid grid-cols-3 gap-2 sm:max-w-[400px]">
                    {[
                      { id: 'ocean' as const, label: t('color.ocean'), color: '#007d8b' },
                      { id: 'ash' as const, label: t('color.ash'), color: '#64748b' },
                      { id: 'green' as const, label: t('color.green'), color: '#17a294' },
                      { id: 'blue' as const, label: t('color.blue'), color: '#3b82f6' },
                      { id: 'yellow' as const, label: t('color.yellow'), color: '#f59e0b' },
                      { id: 'purple' as const, label: t('color.purple'), color: '#a855f7' },
                      { id: 'orange' as const, label: t('color.orange'), color: '#f97316' },
                      { id: 'red' as const, label: t('color.red'), color: '#ef4444' },
                      { id: 'pink' as const, label: t('color.pink'), color: '#ec4899' }
                    ].map((scheme) => {
                    const isActive = scheme.id === colorScheme;
                    return (
                      <button
                        key={scheme.id}
                        type="button"
                        role="radio"
                        aria-checked={isActive}
                        onClick={() => { setColorScheme(scheme.id); void patchPreferences({ colorScheme: scheme.id }); }}
                        className={[
                        'flex flex-col items-center gap-1.5 rounded-xl border p-3 text-center transition-[background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                        isActive ?
                        'border-brand-500 bg-brand-50 font-semibold text-brand-800 shadow-sm' :
                        'border-slate-300 text-slate-600 hover:bg-slate-50'].
                        join(' ')}>
                          <span 
                            className="h-8 w-8 rounded-full border border-slate-300"
                            style={{ backgroundColor: scheme.color }}
                          />
                          <span className="text-fine font-medium">{scheme.label}</span>
                        </button>);
                  })}
                  </div>
                  
                  <div className="space-y-2 pt-2">
                    <label className="flex items-center gap-3 text-body-xs font-medium text-slate-700">
                      <input
                        type="color"
                        value={customColor}
                        onChange={(e) => {
                          setCustomColor(e.target.value);
                          setColorScheme('custom');
                          void patchPreferences({ colorScheme: 'custom', customColor: e.target.value });
                        }}
                        className="h-8 w-8 rounded cursor-pointer border-0 p-0"
                      />
                      {t('color.pickCustom')}
                    </label>
                    <p className="text-caption text-slate-500">Choose your preferred accent color scheme or pick a custom color</p>
                  </div>
                </div>

                <div className="space-y-3">
                  <h3 className="text-control font-semibold text-slate-900">
                    {t('header.language')}
                  </h3>
                  <ul className="flex flex-col gap-2 sm:max-w-[360px]">
                    {languages.map((language) => {
                    const isActive = language.code === locale;
                    return (
                      <li key={language.code}>
                          <button
                          type="button"
                          role="radio"
                          aria-checked={isActive}
                          onClick={() => { setLocale(language.code); void patchPreferences({ language: language.code }); }}
                          className={[
                          'flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-body-xs transition-[background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                          isActive ?
                          'border-brand-500 bg-brand-50 font-semibold text-brand-800 shadow-sm' :
                          'border-slate-300 text-slate-600 hover:bg-slate-50'].
                          join(' ')}>
                            <span className="flex items-center gap-3">
                              <span className="flex h-6 w-8 items-center justify-center rounded-lg bg-slate-100 text-fine font-semibold text-slate-600">
                                {language.short}
                              </span>
                              {language.label}
                            </span>
                            {isActive && <CheckIcon className="h-5 w-5 text-brand-600" strokeWidth={2.25} />}
                          </button>
                        </li>);

                  })}
                  </ul>
                </div>

                <div className="space-y-3">
                  <h3 className="text-control font-semibold text-slate-900">Density</h3>
                  <div className="flex gap-2 rounded-xl border border-slate-200 p-1.5 sm:max-w-[360px]">
                    {(['comfortable', 'compact'] as const).map((option) =>
                  <button
                    key={option}
                    type="button"
                    aria-pressed={density === option}
                    onClick={() => { setDensity(option); void patchPreferences({ density: option }); }}
                    className={[
                    'flex-1 rounded-lg px-4 py-2.5 text-body-xs capitalize transition-[background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    density === option ?
                    'bg-brand-600 font-semibold text-white shadow-sm' :
                    'font-medium text-slate-600 hover:bg-slate-100'].
                    join(' ')}>
                        {option}
                      </button>
                  )}
                  </div>
                  <p className="text-caption text-slate-500">Adjust how much content fits on your screen</p>
                </div>

                <div className="space-y-3">
                  <label
                    htmlFor="pref-default-location"
                    className="block text-control font-semibold text-slate-900"
                  >
                    Default location
                  </label>
                  <select
                    id="pref-default-location"
                    value={defaultLocation}
                    onChange={(e) => {
                      setDefaultLocation(e.target.value);
                      void patchPreferences({ default_location: e.target.value || null });
                    }}
                    className={`${inputClass} sm:max-w-[360px]`}
                  >
                    <option value="">No default (ask each time)</option>
                    {prefLocations.map((location) => (
                      <option key={location.id} value={location.id}>
                        {location.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-caption text-slate-500">
                    Where forecasts, ingestion and the assistant open when you have not picked a site.
                  </p>
                </div>

                <div className="border-t border-slate-200 pt-6 space-y-3">
                  <h3 className="text-control font-semibold text-slate-900">
                    Cookies &amp; legal
                  </h3>
                  <p className="text-body-xs leading-relaxed text-slate-600">
                    {consent ?
                  `Current choice: preferences ${consent.preferences ? 'on' : 'off'}, analytics ${consent.analytics ? 'on' : 'off'}.` :
                  'No cookie choice recorded on this device yet.'}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                    type="button"
                    onClick={reviewConsent}
                    className="rounded-xl border border-slate-300 px-4 py-2.5 text-body-xs font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                      Change cookie choices
                    </button>
                    {(
                  [
                  ['Privacy Policy', 'privacy'],
                  ['Terms of Service', 'terms'],
                  ['Cookie Policy', 'cookies']] as
                  const).
                  map(([label, id]) =>
                  <button
                    key={id}
                    type="button"
                    onClick={() => openLegal(id)}
                    className="rounded-xl px-3 py-2.5 text-body-xs font-semibold text-slate-500 underline-offset-2 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        {label}
                      </button>
                  )}
                  </div>
                </div>
              </div>
            </Panel>
          }

          {tab === 'notifications' &&
          <Panel
            title="Notifications"
            description="Where DemandIQ is allowed to reach you.">

              {notifError && (
                <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                  <p className="text-body-xs font-medium text-red-700">{notifError}</p>
                </div>
              )}
              <ul className="flex flex-col gap-3">
                {[
              {
                label: 'Email digest',
                detail: 'A daily summary of demand and ingestion health.',
                checked: emailDigest,
                toggle: () => { const v = !emailDigest; setEmailDigest(v); void patchNotifications({ email_digest: v }); }
              },
              {
                label: 'In-app alerts',
                detail: 'Critical ingestion and forecast alerts as they happen.',
                checked: pushAlerts,
                toggle: () => { const v = !pushAlerts; setPushAlerts(v); void patchNotifications({ push_alerts: v }); }
              },
              {
                label: 'Weekly report email',
                detail: 'Demand summary delivered every Monday at 07:00.',
                checked: weeklyReport,
                toggle: () => { const v = !weeklyReport; setWeeklyReport(v); void patchNotifications({ weekly_report: v }); }
              }].
              map((row) =>
              <li
                key={row.label}
                className="flex items-start justify-between gap-4 rounded-xl border border-slate-200 px-4 py-4 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:border-slate-300">
                    <div className="min-w-0 flex-1">
                      <p className="text-control font-semibold text-slate-900">
                        {row.label}
                      </p>
                      <p className="mt-1 text-caption text-slate-500">{row.detail}</p>
                    </div>
                    <Toggle checked={row.checked} onChange={row.toggle} label={row.label} />
                  </li>
              )}
              </ul>
            </Panel>
          }

          {tab === 'security' &&
          <Panel title="Security" description="Protect the workspace and its data.">
              <div className="flex flex-col gap-4">
                <div className="rounded-2xl border border-slate-200 px-4 py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-control font-semibold text-slate-900">
                          Two-factor authentication
                        </p>
                        <StatusPill tone={twoFactor ? 'success' : 'warning'} dot>
                          {twoFactor ? 'Enabled' : 'Off'}
                        </StatusPill>
                      </div>
                      <p className="mt-1 text-caption text-slate-500">
                        Require a one-time code at every sign-in. Required for Administrator seats.
                      </p>
                    </div>
                    <Toggle
                    checked={twoFactor}
                    onChange={async () => {
                      if (twoFactor) {
                        try {
                          await api.delete('/api/v1/me/mfa');
                          setTwoFactor(false);
                          toast('Two-factor disabled', 'info');
                        } catch (e) {
                          const msg = e instanceof ApiException ? e.error.message : e instanceof Error ? e.message : 'Failed to disable';
                          toast(msg, 'error');
                        }
                      } else {
                        setDialog('mfa-setup');
                        handleMfaEnroll();
                      }
                    }}
                    label="Two-factor authentication" />
                  </div>

                  {twoFactor &&
                <div className="mt-4 border-t border-slate-100 pt-4">
                      <ul className="flex flex-col gap-3">
                        <li className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <span className="block text-body-xs font-medium text-slate-800">
                              Authenticator app
                            </span>
                            <span className="block text-caption text-slate-500">
                              Active · codes refresh every 30s
                            </span>
                          </div>
                          <button
                        type="button"
                        onClick={() => {
                          setMfaError(undefined);
                          setMfaFieldError(undefined);
                          setMfaReference(undefined);
                          setMfaCode('');
                          setDialog('mfa-setup');
                          handleMfaEnroll();
                        }}
                        className="rounded-xl border border-slate-300 px-3 py-2 text-caption font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                            Reconfigure
                          </button>
                        </li>
                        <li className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <span className="block text-body-xs font-medium text-slate-800">
                              Recovery codes
                            </span>
                            <span className="block text-caption text-slate-500">
                              10 recovery codes · keep them offline
                            </span>
                          </div>
                          <button
                        type="button"
                        onClick={() => setDialog('recovery')}
                        className="rounded-xl border border-slate-300 px-3 py-2 text-caption font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                            Regenerate
                          </button>
                        </li>
                      </ul>
                    </div>
                }
                </div>

                <div className="rounded-2xl border border-slate-200 px-4 py-4">
                  <p className="text-control font-semibold text-slate-900">
                    Connected sign-in methods
                  </p>
                  {identitiesLoading ? (
                    <div className="mt-3"><LoadingState label="Loading sign-in methods" size="sm" /></div>
                  ) : identitiesError ? (
                    <div className="mt-3"><EmptyState variant="error" size="sm" title="Failed to load" description={identitiesError} /></div>
                  ) : (
                  <div className="mt-3 flex flex-col gap-3">
                    {/* Google: every seat links the same way, no role gate. */}
                    <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-slate-200 px-3 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-body-xs font-medium text-slate-800">Google</p>
                        <p className="mt-0.5 text-caption leading-relaxed text-slate-500">
                          {googleLinked
                            ? `Connected. Sign in with Google is enabled for ${user.email}.`
                            : 'Not connected. Link any verified Google account to enable one-click sign-in.'}
                        </p>
                        {googleLinked && linkedOnLabel(googleLinkedAt) && (
                          <p className="mt-0.5 text-caption text-slate-500">
                            Linked {linkedOnLabel(googleLinkedAt)}.
                          </p>
                        )}
                        {!googleLinked && !isGoogleConfigured() && (
                          <p className="mt-1 text-fine text-amber-600">Google sign-in is not configured for this deployment (VITE_GOOGLE_CLIENT_ID missing).</p>
                        )}
                      </div>
                      {googleLinked ? (
                        <button
                          type="button"
                          onClick={() => setDialog('disconnect')}
                          className="shrink-0 rounded-xl border border-slate-300 px-3 py-2 text-caption font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                          Disconnect
                        </button>
                      ) : isGoogleConfigured() ? (
                        <div className="shrink-0">
                          <GoogleButton
                            label="Connect Google"
                            mode="signup"
                            intent="link"
                            onLinkSuccess={() => {
                              setGoogleLinked(true);
                              setIdentitiesError(null);
                              toast('Google account linked. You can now sign in with Google.', 'success');
                            }}
                          />
                        </div>
                      ) : (
                        <button
                          type="button"
                          disabled
                          className="shrink-0 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-caption font-semibold text-slate-500">
                          Connect
                        </button>
                      )}
                    </div>
                    {/* Password: always required. */}
                    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-3">
                      <div className="min-w-0">
                        <p className="text-body-xs font-medium text-slate-800">Email and password</p>
                        <p className="mt-0.5 text-caption text-slate-500">Primary credential for password sign-in and recovery.</p>
                      </div>
                      <span className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-caption font-semibold text-slate-500">Required</span>
                    </div>
                  </div>
                  )}
                </div>

                <div className="rounded-2xl border border-slate-200 px-4 py-4">
                  <p className="text-control font-semibold text-slate-900">Bot protection</p>
                  <p className="mt-1 text-caption text-slate-500">
                    A Turnstile challenge runs on every sign-in and sign-up. It is applied platform-wide, not per workspace.
                  </p>
                  <PermissionNote className="mt-3">
                    Managed by the site administrator in the Site Admin console → Settings → Registration & seats.
                  </PermissionNote>
                </div>

                <div className="rounded-2xl border border-slate-200 px-4 py-4">
                  <p className="text-control font-semibold text-slate-900">Password</p>
                  <p className="mt-1 text-caption text-slate-500">
                    Changing your password signs out your other sessions.
                  </p>
                  <button
                  type="button"
                  onClick={() => setDialog('password')}
                  className="mt-3 rounded-xl border border-slate-300 px-4 py-2.5 text-body-xs font-semibold text-slate-600 transition-[background-color,border-color,color,box-shadow] duration-200 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    Change password
                  </button>
                </div>

                <div className="rounded-2xl border border-slate-200 px-4 py-4">
                  <p className="text-control font-semibold text-slate-900">
                    Active sessions
                  </p>
                  {sessionsLoading ? (
                    <div className="mt-3"><LoadingState label="Loading sessions" size="sm" /></div>
                  ) : sessionsError ? (
                    <div className="mt-3"><EmptyState variant={sessionsError.variant} size="sm" title={sessionsError.variant === 'offline' ? 'Offline' : 'Failed to load'} description={sessionsError.message} /></div>
                  ) : sessions.length === 0 ? (
                    <div className="mt-3"><EmptyState variant="empty" size="sm" title="No sessions" description="No active sessions." /></div>
                  ) : (
                  <>
                  <div className="mt-3">
                  <FilterBar
                    search={sessionList.search}
                    onSearch={sessionList.setSearch}
                    searchPlaceholder="Search sessions…"
                    filters={sessionFilters}
                    values={sessionList.filterValues}
                    onFilterChange={sessionList.setFilter}
                    onClear={sessionList.clearFilters}
                    activeCount={sessionList.activeFilterCount}
                    countLabel={sessionList.filtered !== sessionList.total ? `${sessionList.filtered} of ${sessionList.total} sessions` : undefined}>
                    <ViewSwitcher value={sessionList.view} onChange={sessionList.setView} />
                  </FilterBar>
                  </div>
                  {sessionList.view === 'table' ? (
                  <ResponsiveTable<DeviceSession>
                    className="mt-3"
                    columns={sessionColumns}
                    rows={sessionList.pageRows}
                    rowKey={(session: DeviceSession)=>String(session.id)}
                    caption="Active sessions"
                    minWidth="min-w-[440px]"
                  />
                  ) : (
                  <ul className={sessionList.view === 'cards' ? 'mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2' : 'mt-3 flex flex-col gap-3'}>
                    {sessionList.pageRows.map((session) =>
                  <li
                    key={session.id}
                    className="flex flex-wrap items-center justify-between gap-3 text-body-xs">
                        <span className="min-w-0 flex-1 truncate text-slate-700">
                          {session.device}
                          {session.origin && <span className="ml-2 text-caption text-slate-500">Signed in from {session.origin}</span>}
                        </span>
                        <span className="flex items-center gap-2 text-slate-500">
                          {session.time}
                          {/* `trusted_device` is the login's "remember this
                              device" flag, so it is an extra badge, never a
                              reason to withhold the revoke control, which it
                              used to stand in for. */}
                          {session.trusted && <StatusPill tone="success">Trusted device</StatusPill>}
                          {session.current ?
                      <StatusPill tone="success">This device</StatusPill> :
                          session.revocable ?
                          <button
                            type="button"
                            onClick={() => {
                              setRevokingId(session.id);
                              setDialog('revoke');
                            }}
                            className="rounded-lg font-semibold text-red-600 transition-colors duration-200 ease-out hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500">
                              Revoke
                            </button> :
                          <StatusPill tone="neutral">Not revocable</StatusPill>
                          }
                        </span>
                      </li>
                  )}
                  </ul>
                  )}
                  <Pagination
                    page={sessionList.page}
                    pageCount={sessionList.pageCount}
                    onPage={sessionList.setPage}
                    total={sessionList.filtered}
                    from={sessionList.from}
                    to={sessionList.to}
                    noun="sessions"
                  />
                  </>
                  )}
                </div>
              </div>
            </Panel>
          }

          {tab === 'security' &&
          <Panel title="Danger zone" description="Irreversible actions on your own account.">
              <div className="rounded-2xl border border-red-200 bg-red-50/40 px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-control font-semibold text-slate-900">
                      Delete your account
                    </p>
                    <p className="mt-1 text-caption text-slate-600">
                      Permanently erases your profile, sessions and linked sign-in methods.
                      If you are the last member of your workspace, that workspace and its
                      data are deleted too. This cannot be undone.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDialog('delete-account')}
                    className="shrink-0 rounded-xl border border-red-300 bg-white px-4 py-2.5 text-body-xs font-semibold text-red-600 transition-colors duration-150 ease-out hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                  >
                    Delete account
                  </button>
                </div>
              </div>
          </Panel>
          }
        </div>
      </div>

      <Modal
        open={dialog === 'photo'}
        onClose={() => { setAvatarError(null); setAvatarFile(null); close(); }}
        title="Change photo"
        description="Square images work best. Max 5 MB. Image only."
        size="sm"
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => { setAvatarError(null); setAvatarFile(null); close(); }}>
              Cancel
            </ModalButton>
            <ModalButton
            disabled={avatarUploading}
            onClick={() => void handleAvatarUpload()}>
            
              {avatarUploading ? 'Uploading…' : 'Upload'}
            </ModalButton>
          </>
        }>
        
        <div className="flex flex-col items-center rounded-xl border-2 border-dashed border-slate-300 px-4 py-6 text-center">
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-brand-700 text-h3 font-semibold text-white">
            {user.initials}
          </span>
          <p className="mt-3 text-meta text-slate-600">
            Drag an image here, or choose a file.
          </p>
          <input
            type="file"
            accept="image/*"
            aria-label="Profile photo"
            onChange={(e) => handleAvatarFile(e.target.files?.[0] ?? null)}
            className="mt-3 w-full text-caption text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-caption file:font-semibold file:text-white" />
          {avatarFile && <p className="mt-2 text-fine text-slate-600">Selected: {avatarFile.name} ({(avatarFile.size/1024).toFixed(0)} KB)</p>}
          {avatarError && <div className="mt-2 w-full"><EmptyState variant="error" size="sm" title="Upload failed" description={avatarError} /></div>}
          
        </div>
      </Modal>

      <Modal
        open={dialog === 'password'}
        onClose={() => { setPwFieldErrors({}); setPwApiError(null); setPwApiRef(null); close(); }}
        title="Change password"
        description="You will stay signed in on this device."
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => { setPwFieldErrors({}); setPwApiError(null); close(); }}>
              Cancel
            </ModalButton>
            <ModalButton onClick={() => void handlePasswordUpdate()} disabled={pwSaving}>
              {pwSaving ? 'Updating…' : 'Update password'}
            </ModalButton>
          </>
        }>
        
        <div className="flex flex-col gap-4">
          {pwApiError && (
            <EmptyState variant="error" size="sm" title="Password update failed" description={pwApiError + (pwApiRef ? ` (ref: ${pwApiRef})` : '')} />
          )}
          <FormField id="pw-current" label="Current password" error={pwFieldErrors.current_password}>
            <input
              id="pw-current"
              type="password"
              value={pwCurrent}
              onChange={(e) => {
                setPwCurrent(e.target.value);
                setPwFieldErrors((p) => {
                  const n = { ...p };
                  delete n.current_password;
                  return n;
                });
              }}
              className={[inputClass, pwFieldErrors.current_password ? inputErrorClass : ''].filter(Boolean).join(' ')}
            />
          </FormField>
          <FormField
            id="pw-new"
            label="New password"
            error={pwFieldErrors.new_password}
            hint={pwFieldErrors.new_password ? undefined : 'At least 8 characters, max 128.'}>
            <input
              id="pw-new"
              type="password"
              value={pwNew}
              onChange={(e) => {
                setPwNew(e.target.value);
                setPwFieldErrors((p) => {
                  const n = { ...p };
                  delete n.new_password;
                  delete n.confirm;
                  return n;
                });
              }}
              className={[inputClass, pwFieldErrors.new_password ? inputErrorClass : ''].filter(Boolean).join(' ')}
            />
          </FormField>
          <FormField id="pw-confirm" label="Confirm new password" error={pwFieldErrors.confirm}>
            <input
              id="pw-confirm"
              type="password"
              value={pwConfirm}
              onChange={(e) => {
                setPwConfirm(e.target.value);
                setPwFieldErrors((p) => {
                  const n = { ...p };
                  delete n.confirm;
                  return n;
                });
              }}
              className={[inputClass, pwFieldErrors.confirm ? inputErrorClass : ''].filter(Boolean).join(' ')}
            />
          </FormField>
        </div>
      </Modal>

      <Modal
        open={dialog === 'mfa-setup'}
        onClose={() => {
          close();
          setMfaCode('');
          setMfaError(undefined);
          setMfaFieldError(undefined);
          setMfaReference(undefined);
        }}
        title="Set up authenticator"
        description="Scan the code in your authenticator app, then enter the 6-digit code."
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => {
              close();
              setMfaCode('');
              setMfaError(undefined);
              setMfaFieldError(undefined);
            }}>
              Cancel
            </ModalButton>
            {!mfaRecoveryCodes && (
              <ModalButton onClick={handleMfaConfirm} disabled={mfaLoading || !mfaEnroll}>
                {mfaLoading ? 'Verifying…' : 'Verify and save'}
              </ModalButton>
            )}
          </>
        }>
        
        <div className="flex flex-col items-center gap-3">
          {mfaEnroll?.provisioning_uri ? (
            <div className="rounded-lg border border-slate-200 bg-white p-2">
              <QRCodeSVG value={mfaEnroll.provisioning_uri} size={160} level="M" includeMargin />
            </div>
          ) : mfaLoading ? (
            <LoadingState label="Generating authenticator setup" size="sm" className="!py-6" />
          ) : mfaError ? (
            <div className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed border-red-200 bg-red-50 px-4 py-6">
              <p className="text-center text-meta text-red-700">{mfaError}</p>
              <button
                type="button"
                onClick={() => void handleMfaEnroll()}
                className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-caption font-semibold text-red-700 hover:bg-red-50"
              >
                Retry
              </button>
            </div>
          ) : (
            <div className="flex h-40 w-40 items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50">
              <p className="px-3 text-center text-micro text-slate-500">Starting enrollment…</p>
            </div>
          )}
          <p className="text-center text-micro text-slate-500">
            {mfaEnroll?.provisioning_uri ? 'Scan with Google Authenticator, Authy, or 1Password. Then enter the 6-digit code.' : 'Or enter this key manually:'}
            <br />
            <code className="font-mono text-caption text-slate-800">
              {mfaEnroll?.secret ? mfaEnroll.secret.match(/.{1,4}/g)?.join(' ') : mfaLoading ? 'Generating…' : 'Enroll to generate a secret'}
            </code>
          </p>
          {mfaEnroll?.provisioning_uri && (
            <details className="w-full">
              <summary className="cursor-pointer text-center text-fine font-medium text-slate-500 hover:text-slate-700">Show provisioning URI</summary>
              <p className="mt-1 max-w-full break-all text-center text-fine text-slate-500">{mfaEnroll.provisioning_uri}</p>
            </details>
          )}
          {!mfaRecoveryCodes && (
            <FormField id="mfa-code" label="6-digit code" error={mfaFieldError}>
              <input
                id="mfa-code"
                value={mfaCode}
                onChange={(e) => {
                  setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6));
                  setMfaFieldError(undefined);
                }}
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                required
                placeholder="000000"
                autoComplete="one-time-code"
                className={`${inputClass} text-center tracking-[0.2em] tabular-nums`}
              />
            </FormField>
          )}
          {mfaError && (
            <EmptyState variant="error" size="sm" title="Verification failed" description={mfaError} hints={mfaReference ? [`Reference: ${mfaReference}`] : undefined} />
          )}
          {mfaRecoveryCodes && (
            <div className="w-full rounded-xl border border-amber-200 bg-amber-50 p-3.5">
              <p className="text-body-xs font-semibold text-amber-900">Recovery codes: save these now (shown once)</p>
              <ul className="mt-2 grid grid-cols-2 gap-1.5 font-mono text-caption text-slate-800">
                {mfaRecoveryCodes.map((c) => (
                  <li key={c} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">{c}</li>
                ))}
              </ul>
              <button
                type="button"
                onClick={close}
                className="mt-3 w-full rounded-xl bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                I saved my codes
              </button>
            </div>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={dialog === 'recovery'}
        onClose={close}
        onConfirm={handleRegenerateRecovery}
        title="Regenerate recovery codes?"
        body="Your existing 8 unused codes will stop working immediately. You will be shown the new set once."
        confirmLabel="Regenerate" />
      

      <ConfirmDialog
        open={dialog === 'disconnect'}
        onClose={close}
        onConfirm={() => void handleDisconnectGoogle()}
        title="Disconnect Google?"
        body="You will need your email and password to sign in. Your workspace and data are unaffected."
        confirmLabel="Disconnect"
        tone="danger" />
      

      <ConfirmDialog
        open={dialog === 'revoke'}
        onClose={close}
        onConfirm={() => void handleRevokeSession()}
        title="Revoke this session?"
        body="That device will be signed out immediately and will need to authenticate again."
        confirmLabel="Revoke session"
        tone="danger" />

      <DeleteAccountDialog
        open={dialog === 'delete-account'}
        onClose={close}
        hasPassword={user?.hasPassword !== false}
        onConfirm={handleDeleteAccount} />

    </>);

}