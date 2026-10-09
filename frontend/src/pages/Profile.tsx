import { useEffect, useRef, useState, useMemo } from 'react';
import {
  UserIcon,
  MapPinIcon,
  ClockIcon,
  ShieldCheckIcon,
  CameraIcon,
  MonitorIcon,
  SmartphoneIcon } from
'lucide-react';
import { Panel } from '../components/ui/Panel';
import { StatusPill } from '../components/ui/StatusPill';
import { Modal, ModalButton } from '../components/ui/Modal';
import { Field, inputClass } from '../components/ui/Field';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { api, ApiException } from '../api/client';
import { EmptyState } from '../components/ui/EmptyState';
import { auditActionLabel, auditDetailLabel, plainStatus } from '../lib/plainLanguage';
import { jobLabel } from '../lib/schedule';
import { mapSessions, type DeviceSession } from '../lib/sessions';

interface ProfileProps {
  onNavigate?: (id: string) => void;
}

const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;  const phoneRe = /^\+?[0-9\s-]{7,32}$/;

interface LocationItem { name: string; area: string; access: string }
interface ActivityItem { action: string; detail: string; time: string }

function timeAgo(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const mins = Math.round(diff/60000);
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins/60);
    if (hrs < 24) return `${hrs} hours ago`;
    const days = Math.round(hrs/24);
    return `${days} days ago`;
  } catch { return iso ?? ''; }
}

function monthsSince(iso?: string | null): number | null {
  if (!iso) return null;
  try {
    const created = new Date(iso);
    if (Number.isNaN(created.getTime())) return null;
    const now = new Date();
    let months = (now.getFullYear() - created.getFullYear()) * 12 + (now.getMonth() - created.getMonth());
    // adjust if day not yet reached
    if (now.getDate() < created.getDate()) months = Math.max(0, months - 1);
    if (months < 0) return 0;
    return months;
  } catch { return null; }
}

function memberLabel(createdAt?: string | null): string {
  const m = monthsSince(createdAt ?? null);
  if (m === null) return '—';
  if (m === 0) return '<1 mo';
  return `${m} mo`;
}

export function Profile({ onNavigate }: ProfileProps) {
  const { user, setUser, refresh } = useSession();
  const { toast } = useToast();
  // Optional reads on this page, each gated on the capability its endpoint
  // enforces. A seat that lacks one gets the same "—"/empty state it used to
  // reach by sending the request, taking a 403 and discarding it, minus the
  // rejected requests in the console.
  const canReadIngest = (user.permissions ?? []).includes('ingest:write');
  const canReadAudit = (user.permissions ?? []).includes('audit:read');
  const canReadPipeline = (user.permissions ?? []).includes('pipeline:read');
  const [photoOpen, setPhotoOpen] = useState(false);

  // hydrate form from live user data – not hard-coded
  const initialTitle = (user as any).job_title ?? (user as any).jobTitle ?? '';
  const initialPhone = (user as any).phone ?? '';
  // bio: backend has no bio column; keep localStorage fallback, fallback to the dash placeholder, never hardcoded sentence
  const storedBio = (() => { try { return localStorage.getItem('demandiq_bio') ?? ''; } catch { return ''; } })();
  const initialBio = (user as any).bio ?? storedBio ?? '';

  const [form, setForm] = useState({
    name: user.name,
    title: initialTitle,
    phone: initialPhone,
    email: user.email,
    bio: initialBio,
  });
  const [saved, setSaved] = useState(true);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiRef, setApiRef] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Keep form in sync when user hydrates from /auth/me after mount
  const hasSyncedRef = useRef(false);
  useEffect(() => {
    if (hasSyncedRef.current) return;
    // only sync once when not dirty
    if (!saved) return;
    const t = (user as any).job_title ?? (user as any).jobTitle ?? '';
    const p = (user as any).phone ?? '';
    const b = (user as any).bio ?? storedBio ?? '';
    if (t !== form.title || p !== form.phone || user.name !== form.name || user.email !== form.email || b !== form.bio) {
      setForm({ name: user.name, title: t ?? '', phone: p ?? '', email: user.email, bio: b ?? '' });
    }
    hasSyncedRef.current = true;
  }, [user, saved]); // eslint-disable-line react-hooks/exhaustive-deps

  // Locations state
  const [locations, setLocations] = useState<LocationItem[] | null>(null);
  const [locLoading, setLocLoading] = useState(true);
  const [locError, setLocError] = useState<{ variant: 'error'|'offline'; message: string; reference?: string | null } | null>(null);
  // Activity state
  const [activity, setActivity] = useState<ActivityItem[] | null>(null);
  const [actLoading, setActLoading] = useState(true);
  const [actError, setActError] = useState<{ variant: 'error'|'offline'; message: string; reference?: string | null } | null>(null);
  // Sessions state
  const [sessions, setSessions] = useState<DeviceSession[] | null>(null);
  const [sessLoading, setSessLoading] = useState(true);
  const [sessError, setSessError] = useState<{ variant: 'error'|'offline'; message: string; reference?: string | null } | null>(null);
  // Imports KPI state
  const [importsCount, setImportsCount] = useState<number | null>(null);
  const [impLoading, setImpLoading] = useState(true);
  const [impError, setImpError] = useState<{ variant: 'error'|'offline'; message: string; reference?: string | null } | null>(null);

  // Avatar upload
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarUploading, setAvatarUploading] = useState(false);

  const update = (key: keyof typeof form, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    setSaved(false);
    setFieldErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      if (key === 'title') delete next['job_title'];
      return next;
    });
    setApiError(null);
    setApiRef(null);
  };

  const validate = (): Record<string,string> => {
    const err: Record<string,string> = {};
    const name = form.name.trim();
    if (!name) err.name = 'Name is required';
    else if (name.length < 1 || name.length > 200) err.name = 'Name must be 1–200 characters';
    if (form.email.trim()) {
      if (!emailRe.test(form.email.trim())) err.email = 'Invalid email address';
      else if (form.email.length > 254) err.email = 'Email too long';
    } else {
      err.email = 'Email is required';
    }
    if (form.phone.trim()) {
      if (form.phone.length > 32) err.phone = 'Phone must be ≤ 32 characters';
      else if (!phoneRe.test(form.phone.trim())) err.phone = 'Phone must be 7–32 chars, digits + - space, optional +';
    }
    if (form.title.trim().length > 200) err.job_title = 'Job title must be ≤ 200 characters';
    else if (form.title && form.title.trim().length === 0 && form.title.length > 0) err.job_title = 'Job title cannot be blank';
    if (form.bio.trim().length > 500) err.bio = 'Bio must be ≤ 500 characters';
    return err;
  };

  const handleSave = async () => {
    const v = validate();
    if (Object.keys(v).length > 0) {
      setFieldErrors(v);
      return;
    }
    setSaving(true);
    setFieldErrors({});
    setApiError(null);
    setApiRef(null);
    try {
      const payload: Record<string, string> = {};
      payload.name = form.name.trim();
      payload.email = form.email.trim().toLowerCase();
      if (form.phone.trim()) payload.phone = form.phone.trim();
      if (form.title.trim()) payload.job_title = form.title.trim();
      // bio: backend ProfileUpdate has no bio field (extra=ignore), but include it so it is not discarded.
      // If backend later adds bio, it will persist; otherwise we keep localStorage fallback.
      if (form.bio.trim()) (payload as any).bio = form.bio.trim();

      let patched: any = null;
      try {
        patched = await api.patch<any>('/api/v1/me/profile', payload);
      } catch (e) {
        // if backend rejects unknown bio field, retry without it and persist locally
        if (e instanceof ApiException && e.status === 422) {
          const details: any = (e.error as any)?.details;
          const hasBioError = Array.isArray(details) && details.some((d:any)=> d.field === 'bio') || e.error.field === 'bio';
          if (hasBioError || (e.error.message && e.error.message.toLowerCase().includes('bio'))) {
            // `bio` is destructured only to keep it out of `rest`.
            const { bio: _bio, ...rest } = payload as any;
            patched = await api.patch<any>('/api/v1/me/profile', rest);
          } else {
            throw e;
          }
        } else throw e;
      }
      // persist bio locally regardless (so UI is real)
      try { localStorage.setItem('demandiq_bio', form.bio.trim()); } catch {}
      setSaved(true);
      // optimistic update + refresh to get canonical job_title/phone/created_at
      setUser({ ...user, name: payload.name, email: payload.email, phone: (payload as any).phone ?? (user as any).phone, job_title: (payload as any).job_title ?? (user as any).job_title, jobTitle: (payload as any).job_title ?? (user as any).jobTitle, bio: form.bio.trim() } as any);
      try { await refresh(); } catch {}
      // also update from patched response if it contains fields
      if (patched && typeof patched === 'object') {
        const u: any = patched.user ?? patched;
        if (u && (u.job_title || u.phone || u.created_at)) {
          setUser((prev: any) => ({ ...prev, job_title: u.job_title ?? prev.job_title, jobTitle: u.job_title ?? prev.jobTitle, phone: u.phone ?? prev.phone, created_at: u.created_at ?? prev.created_at, createdAt: u.created_at ?? prev.createdAt } as any));
        }
      }
      toast('Profile updated.');
    } catch (e) {
      if (e instanceof ApiException) {
        const field = e.error.field;
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        if (field) setFieldErrors({ [field]: e.error.message });
        setApiError(msg);
        setApiRef(e.reference ?? null);
        const details = (e.error as any)?.details;
        if (Array.isArray(details)) {
          const map: Record<string,string> = {};
          for (const d of details) if (d.field) map[d.field] = d.message;
          if (Object.keys(map).length) setFieldErrors(map);
        }
      } else {
        setApiError(e instanceof Error ? e.message : 'Failed to save profile');
      }
    } finally {
      setSaving(false);
    }
  };

  // Fetch locations
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLocLoading(true); setLocError(null);
      try {
        let data: any = null;
        try {
          data = await api.get<any>('/api/v1/locations', { limit: 100, scope: 'me' } as any);
        } catch (e) {
          if (e instanceof ApiException && (e.status === 400 || e.status === 422 || e.status === 404)) {
            data = await api.get<any>('/api/v1/locations', { limit: 100 } as any);
          } else throw e;
        }
        let list: any[] = Array.isArray(data) ? data : data?.data ?? data?.locations ?? data?.items ?? [];
        const scope = (user as any)?.location_scope ?? (user as any)?.locationScope;
        if (scope && Array.isArray(list)) {
          if (Array.isArray(scope) && scope.length) {
            list = list.filter((l: any) => scope.includes(l.id) || scope.includes(l.name));
          }
        }
        const mapped: LocationItem[] = list.map((l: any) => ({
          name: l.name ?? l.location_name ?? l.title ?? String(l.id ?? 'Location'),
          area: l.area ?? l.district ?? l.region ?? l.address ?? '',
          access: l.access ?? l.role ?? 'Full',
        }));
        if (!cancelled) setLocations(mapped);
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiException) {
            const isOffline = !navigator.onLine || e.status === 0;
            setLocError({ variant: isOffline ? 'offline' : 'error', message: e.error.message, reference: e.reference ?? null });
          } else {
            setLocError({ variant: !navigator.onLine ? 'offline' : 'error', message: e instanceof Error ? e.message : 'Failed to load' });
          }
          setLocations([]);
        }
      } finally { if (!cancelled) setLocLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [user]);

  // Fetch imports count for KPI
  useEffect(() => {
    // `/api/v1/ingest/uploads` sits behind INGEST_WRITE. Without it the KPI has
    // no honest number to show, so it shows the placeholder rather than a load failure.
    if (!canReadIngest) {
      setImportsCount(null);
      setImpError(null);
      setImpLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      setImpLoading(true); setImpError(null);
      try {
        const res: any = await api.get<any>('/api/v1/ingest/uploads', { limit: 100 } as any);
        const list: any[] = Array.isArray(res) ? res : res?.data ?? res?.uploads ?? res?.items ?? [];
        if (!cancelled) setImportsCount(list.length);
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiException) {
            const isOffline = !navigator.onLine || e.status === 0;
            setImpError({ variant: isOffline ? 'offline' : 'error', message: e.error.message, reference: e.reference ?? null });
          } else {
            setImpError({ variant: !navigator.onLine ? 'offline' : 'error', message: e instanceof Error ? e.message : 'Failed to load' });
          }
          setImportsCount(null);
        }
      } finally { if (!cancelled) setImpLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [canReadIngest]);

  // Fetch recent activity
  useEffect(() => {
    // Both feeds behind this panel are administrator reads: audit-log needs
    // AUDIT_READ and a pipeline run needs PIPELINE_READ. A seat holding neither
    // used to fire both and swallow the 403s; the empty state it landed on is
    // what is set directly here.
    if (!canReadAudit && !canReadPipeline) {
      setActivity([]);
      setActError(null);
      setActLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      setActLoading(true); setActError(null);
      try {
        let data: any = null;
        // The audit log is the richer feed, so it is tried first. `actor_id` is
        // best-effort: where the endpoint does not know it, the unfiltered page
        // is still the right answer, so those rejections fall through to it.
        if (canReadAudit) {
          try {
            data = await api.get<any>('/api/v1/audit-log', ((user as any).id ? { limit: 5, actor_id: (user as any).id } : { limit: 5 }) as any);
          } catch (e) {
            if (!(e instanceof ApiException && (e.status === 404 || e.status === 400 || e.status === 422))) throw e;
            data = null;
          }
        }
        // No audit access, or an unavailable audit endpoint: the run list is the
        // fallback, and only for a seat that may read it.
        if (!data && canReadPipeline) {
          data = await api.get<any>('/api/v1/pipeline/runs', { limit: 5 } as any);
        }
        const list: any[] = Array.isArray(data) ? data : data?.data ?? data?.logs ?? data?.items ?? data?.runs ?? [];
        const mapped: ActivityItem[] = list.slice(0,5).map((r: any) => ({
          // An audit row's `action` is the literal request line (`POST /api/v1/agent/messages`)
          // and its `target` is the same path; neither belongs on a personal feed, so both
          // go through the plain-language layer. The second line names what changed and
          // whether it was accepted ("Preferences updated · accepted from the web app")
          // rather than repeating the endpoint.
          //
          // A pipeline row (the fallback feed) has no request line at all: it is
          // named the way Overview names the same rows, "Connector sync · Succeeded".
          // Reading `status` alone rendered the row as "Success", which says
          // nothing about what ran.
          action: r.job_type
            ? `${jobLabel(String(r.job_type))} · ${plainStatus(String(r.status ?? 'queued'))}`
            : auditActionLabel(r.action ?? r.event ?? r.type ?? r.status ?? 'Activity'),
          detail: auditDetailLabel(r),
          time: r.time ?? timeAgo(r.created_at ?? r.timestamp ?? r.at ?? r.started_at),
        }));
        if (!cancelled) setActivity(mapped);
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiException) {
            // viewer/operator gets 403 on audit-log – treat as empty not error, so EmptyState empty is reachable
            if (e.status === 403) {
              setActivity([]);
              setActError(null);
            } else {
              const isOffline = !navigator.onLine || e.status === 0;
              setActError({ variant: isOffline ? 'offline' : 'error', message: e.error.message, reference: e.reference ?? null });
              setActivity([]);
            }
          } else {
            setActError({ variant: !navigator.onLine ? 'offline' : 'error', message: e instanceof Error ? e.message : 'Failed to load' });
            setActivity([]);
          }
        }
      } finally { if (!cancelled) setActLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [user, canReadAudit, canReadPipeline]);

  // Fetch sessions. The row mapping lives in `lib/sessions.ts` because the
  // Settings Security tab lists the same rows and the two copies had drifted.
  const fetchSessions = async () => {
    setSessLoading(true); setSessError(null);
    try {
      const data: any = await api.get<any>('/api/v1/me/sessions');
      setSessions(mapSessions(data));
    } catch (e) {
      if (e instanceof ApiException) {
        const isOffline = !navigator.onLine || e.status === 0;
        setSessError({ variant: isOffline ? 'offline' : 'error', message: e.error.message, reference: e.reference ?? null });
      } else {
        setSessError({ variant: !navigator.onLine ? 'offline' : 'error', message: e instanceof Error ? e.message : 'Failed to load' });
      }
      setSessions([]);
    } finally { setSessLoading(false); }
  };
  useEffect(() => { void fetchSessions(); }, []);

  const handleRevoke = async (id: string) => {
    try {
      await api.delete(`/api/v1/me/sessions/${id}`);
      setSessions((cur) => (cur ?? []).filter((s) => s.id !== id));
      toast('Device signed out.', 'info');
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast(e instanceof Error ? e.message : 'Failed to revoke', 'error');
    }
  };

  const handleAvatarChange = (f: File | null) => {
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
      toast('Profile photo updated.');
      setPhotoOpen(false);
      setAvatarFile(null);
      try { await refresh(); } catch {}
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

  const locCount = locations?.length ?? 0;
  const createdIso = (user as any).created_at ?? (user as any).createdAt ?? null;
  const memberText = useMemo(() => memberLabel(createdIso), [createdIso]);
  const importsText = impLoading ? '…' : impError ? '—' : (importsCount !== null ? String(importsCount) : '—');
  const locationsText = locLoading ? '…' : locError ? '—' : String(locCount);
  const displayTitle = form.title.trim() ? form.title : ((user as any).job_title ?? (user as any).jobTitle ?? '') || '—';
  const displayPhone = form.phone.trim() ? form.phone : ((user as any).phone ?? '') || '';
  // bio display – use form.bio, fallback to stored, else placeholder dash with hint
  const hasBio = form.bio.trim().length > 0;

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <Panel>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div className="relative shrink-0 self-start sm:self-auto">
              <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-700 text-h3 font-bold text-white">
                {user.initials}
              </span>
              <button
                type="button"
                onClick={() => setPhotoOpen(true)}
                aria-label="Change profile photo"
                className="absolute -bottom-1.5 -right-1.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                
                <CameraIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
              </button>
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-h4 font-bold tracking-tight text-slate-900">
                  {form.name || '—'}
                </h2>
                <StatusPill tone="success" dot>
                  {user.role}
                </StatusPill>
              </div>
              <p className="mt-0.5 truncate text-meta text-slate-500">
                {displayTitle} · {form.email || '—'}
              </p>
              {displayPhone && <p className="mt-0.5 text-micro text-slate-500">{displayPhone}</p>}
              {hasBio ? (
                <p className="mt-1.5 text-caption leading-relaxed text-slate-600">
                  {form.bio}
                </p>
              ) : (
                <p className="mt-1.5 text-caption italic leading-relaxed text-slate-500">No bio. Add one in Personal details below.</p>
              )}
            </div>

            <dl className="grid shrink-0 grid-cols-3 gap-2 sm:w-[212px]">
              {[
                ['Locations', locationsText],
                ['Imports', importsText],
                ['Member', memberText],
              ].map(([label, value]) =>
                <div
                  key={label}
                  className="rounded-lg border border-slate-200 px-2 py-1.5 text-center">
                  
                  <dt className="text-fine text-slate-500">{label}</dt>
                  <dd className="text-control font-bold tabular-nums text-slate-900">
                    {value}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        </Panel>

        <Panel
          title="Personal details"
          icon={UserIcon}
          description="Shown to your team on approvals, imports and audit entries."
          action={
          <div className="flex items-center gap-2">
              {!saved &&
            <button
              type="button"
              onClick={() => {
                const t = (user as any).job_title ?? (user as any).jobTitle ?? '';
                const p = (user as any).phone ?? '';
                const b = (user as any).bio ?? (()=>{ try{return localStorage.getItem('demandiq_bio') ?? '';}catch{return ''}})() ?? '';
                setForm({
                  name: user.name,
                  title: t ?? '',
                  phone: p ?? '',
                  email: user.email,
                  bio: b ?? '',
                });
                setSaved(true);
                setFieldErrors({});
                setApiError(null);
              }}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              
                  Discard
                </button>
            }
              <button
              type="button"
              disabled={saved || saving}
              onClick={() => void handleSave()}
              className="rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">
              
                {saving ? 'Saving…' : saved ? 'Saved' : 'Save changes'}
              </button>
            </div>
          }>
          
          {apiError && (
            <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2">
              <p className="text-meta font-medium text-red-700">{apiError}</p>
              {apiRef && <p className="text-fine text-red-600">ref {apiRef}</p>}
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field id="pf-name" label="Full name" hint={fieldErrors.name ? undefined : '1–200 characters'}>
              <input
                id="pf-name"
                type="text"
                value={form.name}
                maxLength={200}
                onChange={(event) => update('name', event.target.value)}
                aria-invalid={Boolean(fieldErrors.name)}
                className={`${inputClass} ${fieldErrors.name ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : ''}`} />
              {fieldErrors.name && <p className="mt-1 text-fine text-red-600" role="alert">{fieldErrors.name}</p>}
            </Field>
            <Field id="pf-title" label="Job title" hint={fieldErrors.job_title ? undefined : '≤ 200 characters (leave empty to show the placeholder)'}>
              <input
                id="pf-title"
                type="text"
                value={form.title}
                maxLength={200}
                placeholder="—"
                onChange={(event) => update('title', event.target.value)}
                aria-invalid={Boolean(fieldErrors.job_title)}
                className={`${inputClass} ${fieldErrors.job_title ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : ''}`} />
              {fieldErrors.job_title && <p className="mt-1 text-fine text-red-600" role="alert">{fieldErrors.job_title}</p>}
            </Field>
            <Field id="pf-email" label="Work email" hint={fieldErrors.email ? undefined : 'Used for sign-in and reports.'}>
              <input
                id="pf-email"
                type="email"
                value={form.email}
                onChange={(event) => update('email', event.target.value)}
                aria-invalid={Boolean(fieldErrors.email)}
                className={`${inputClass} ${fieldErrors.email ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : ''}`} />
              {fieldErrors.email && <p className="mt-1 text-fine text-red-600" role="alert">{fieldErrors.email}</p>}
            </Field>
            <Field id="pf-phone" label="Phone" hint={fieldErrors.phone ? undefined : 'For critical alerts by SMS. ≤32 chars. Leave empty to show the placeholder.'}>
              <input
                id="pf-phone"
                type="tel"
                value={form.phone}
                maxLength={32}
                placeholder="—"
                onChange={(event) => update('phone', event.target.value)}
                aria-invalid={Boolean(fieldErrors.phone)}
                className={`${inputClass} ${fieldErrors.phone ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : ''}`} />
              {fieldErrors.phone && <p className="mt-1 text-fine text-red-600" role="alert">{fieldErrors.phone}</p>}
            </Field>
            <div className="sm:col-span-2">
              <Field id="pf-bio" label="About" hint={fieldErrors.bio ? undefined : 'Max 500 characters. Leave empty to hide.'}>
                <textarea
                  id="pf-bio"
                  rows={2}
                  value={form.bio}
                  maxLength={500}
                  placeholder="Tell your team what you do…"
                  onChange={(event) => update('bio', event.target.value)}
                  aria-invalid={Boolean(fieldErrors.bio)}
                  className={`${inputClass} ${fieldErrors.bio ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : ''}`} />
                {fieldErrors.bio && <p className="mt-1 text-fine text-red-600" role="alert">{fieldErrors.bio}</p>}
              </Field>
              {!hasBio && !fieldErrors.bio && (
                <p className="mt-1.5 text-fine text-slate-500">Bio is empty. A placeholder shows until you add one.</p>
              )}
            </div>
          </div>
        </Panel>

        <Panel
          title="Recent activity"
          icon={ClockIcon}
          description="Your last actions in this workspace.">
          
          {actLoading ? (
            <p className="text-caption text-slate-500">Loading…</p>
          ) : actError ? (
            <EmptyState variant={actError.variant} size="sm" title={actError.variant === 'offline' ? 'Offline' : 'Failed to load'} description={actError.reference ? `${actError.message} (ref: ${actError.reference})` : actError.message} hints={actError.variant === 'offline' ? ['Check your connection','Retry when back online'] : actError.reference ? [`Reference ${actError.reference}`] : undefined} />
          ) : !activity || activity.length === 0 ? (
            <EmptyState variant="empty" size="sm" title="No recent activity" description="Your actions will appear here." hints={['Import data or create a forecast to see activity']} />
          ) : (
          <ol className="relative border-l border-slate-200 pl-4">
            {/* Row identity is the position; a feed carries no id of its own,
                and two actions of the same kind in the same minute (a policy run
                and a preference change) used to collide on action+time. */}
            {activity.map((entry, index) =>
            <li key={`${index}-${entry.action}-${entry.time}`} className="relative pb-3.5 last:pb-0">
                <span className="absolute -left-[21px] mt-1 h-2 w-2 rounded-full bg-brand-500" />
                <p className="text-meta font-semibold text-slate-900">
                  {entry.action}
                </p>
                <p className="mt-0.5 text-micro text-slate-500">
                  {entry.detail} · {entry.time}
                </p>
              </li>
            )}
          </ol>
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-3">
        <Panel
          title="Your locations"
          icon={MapPinIcon}
          description="Sites you can see and change.">
          
          {locLoading ? (
            <p className="text-caption text-slate-500">Loading…</p>
          ) : locError ? (
            <EmptyState variant={locError.variant} size="sm" title={locError.variant === 'offline' ? 'Offline' : 'Failed to load'} description={locError.reference ? `${locError.message} (ref: ${locError.reference})` : locError.message} hints={locError.variant === 'offline' ? ['Check your connection'] : locError.reference ? [`Reference ${locError.reference}`] : undefined} />
          ) : !locations || locations.length === 0 ? (
            <EmptyState variant="empty" size="md" title="No assigned locations" description="No locations assigned to you." hints={['Ask an administrator to assign locations','Check Settings for workspace scope']} />
          ) : (
          <ul className="flex flex-col gap-2">
            {locations.map((location) =>
            <li
              key={location.name}
              className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2">
              
                <span className="min-w-0">
                  <span className="block truncate text-meta font-semibold text-slate-900">
                    {location.name}
                  </span>
                  <span className="block text-fine text-slate-500">
                    {location.area || '—'}
                  </span>
                </span>
                <StatusPill tone={location.access === 'Full' ? 'success' : 'neutral'}>
                  {location.access}
                </StatusPill>
              </li>
            )}
          </ul>
          )}
          <p className="mt-2.5 text-micro leading-relaxed text-slate-500">
            Location access is set by an administrator.
          </p>
        </Panel>

        <Panel
          title="Signed-in devices"
          icon={ShieldCheckIcon}
          description="Revoke anything you don’t recognise.">
          
          {sessLoading ? (
            <p className="text-caption text-slate-500">Loading…</p>
          ) : sessError ? (
            <EmptyState variant={sessError.variant} size="sm" title={sessError.variant === 'offline' ? 'Offline' : 'Failed to load'} description={sessError.reference ? `${sessError.message} (ref: ${sessError.reference})` : sessError.message} hints={sessError.variant === 'offline' ? ['Check your connection'] : sessError.reference ? [`Reference ${sessError.reference}`] : undefined} />
          ) : !sessions || sessions.length === 0 ? (
            <EmptyState variant="empty" size="sm" title="No signed-in devices" description="No signed-in devices yet." hints={['Sign in from another device to see it here']} />
          ) : (
          <ul className="flex flex-col gap-2">
            {sessions.map((session) => {
              const Icon = session.mobile ? SmartphoneIcon : MonitorIcon;
              return (
                <li
                  key={session.id}
                  className="flex items-start gap-2.5 rounded-lg border border-slate-200 px-3 py-2">
                  
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-meta font-semibold text-slate-900">
                      {session.device}
                    </span>
                    <span className="block text-fine text-slate-500">
                      {/* `origin` carries whatever the sign-in recorded (an
                          address in this deployment, which the API used to
                          mislabel `ip_country`), so it is stated as where it
                          came from, not as a country. */}
                      {session.origin ? `Signed in from ${session.origin} · ` : ''}{session.time || 'Last seen'}
                    </span>
                  </span>
                  {/* `trusted_device` is the login's "remember this device"
                      flag, shown beside the pill/revoke pair rather than in
                      place of it. The session this request was made with
                      (`current`) is labelled and is not offered for revoking. */}
                  {session.trusted && <StatusPill tone="success">Trusted</StatusPill>}
                  {session.current ?
                  <StatusPill tone="success" dot>
                      This device
                    </StatusPill> :
                  session.revocable ?
                  <button
                    type="button"
                    onClick={() => void handleRevoke(session.id)}
                    className="shrink-0 rounded-md border border-slate-300 px-2 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    
                      Revoke
                    </button> :
                  <StatusPill tone="neutral">Not revocable</StatusPill>
                  }
                </li>);

            })}
          </ul>
          )}
          {/* The full list (with what "Trusted" and "This device" mean, and a
              way to sign any of the others out) lives on its own screen, so
              this panel keeps the summary and links there instead of being a
              second, partial copy of it. */}
          <button
            type="button"
            onClick={() => onNavigate?.('devices')}
            className="mt-2.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
            
            Manage all devices
          </button>
        </Panel>
      </div>

      <Modal
        open={photoOpen}
        onClose={() => { setPhotoOpen(false); setAvatarError(null); setAvatarFile(null); }}
        title="Change profile photo"
        description="Square images work best. Max 5 MB. Image only."
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => { setPhotoOpen(false); setAvatarError(null); setAvatarFile(null); }}>
              Cancel
            </ModalButton>
            <ModalButton
            disabled={avatarUploading}
            onClick={() => void handleAvatarUpload()}>
            
              {avatarUploading ? 'Uploading…' : 'Upload'}
            </ModalButton>
          </>
        }>
        
        <div className="flex flex-col gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            aria-label="Profile photo"
            onChange={(e) => handleAvatarChange(e.target.files?.[0] ?? null)}
            className="w-full rounded-lg border border-dashed border-slate-300 px-3 py-6 text-caption text-slate-500" />
          {avatarFile && <p className="text-fine text-slate-600">Selected: {avatarFile.name} ({(avatarFile.size/1024).toFixed(0)} KB)</p>}
          {avatarError && <EmptyState variant="error" size="sm" title="Upload failed" description={avatarError} />}
        </div>
      
      </Modal>
    </div>);

}
