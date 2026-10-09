import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiException, setToken } from '../../api/client';
import {
  isGoogleConfigured,
  renderGoogleButton,
  onGoogleCredential,
  isGsiOriginBlockedMessage,
} from '../../lib/google';
import { useToast } from '../../contexts/ToastContext';

interface GoogleButtonProps {
  label: string;
  /**
   * signin: One Tap prompt (lightweight).
   * signup / profile: render the official Google button inside this component
   *   so the account chooser always appears (One Tap can silently pick a
   *   previously-used account — wrong UX for new sign-ups).
   */
  mode?: 'signin' | 'signup' | 'profile';
  /** Called with the signed-in account once the token exchange succeeds. */
  onSuccess?: (account: {
    email: string;
    name: string;
    picture?: string;
    role?: string;
    needsProfile?: boolean;
  }) => void;
  /** When set to 'link', the credential is posted to /me/identities/google
   *  to bind the Google subject to the CURRENT authenticated user, without
   *  replacing the session token. The surrounding Settings page stays signed
   *  in as the same user. */
  intent?: 'login' | 'link';
  onLinkSuccess?: () => void;
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" className="h-4 w-4 shrink-0" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />

      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.94v2.33A9 9 0 0 0 9 18Z" />

      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.94a9 9 0 0 0 0 8.1l3.03-2.33Z" />

      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .94 4.95l3.03 2.33C4.68 5.16 6.66 3.58 9 3.58Z" />

    </svg>);

}

/**
 * Real Google sign-in button.
 *
 * mode="signin" → Google One Tap prompt (falls back to the official GIS
 * button when the prompt cannot be shown). mode="signup"/"profile" → the
 * official Google button rendered in place, so the account chooser always
 * appears. The resulting id_token is verified server-side and exchanged at
 * POST /auth/oauth/google.
 *
 * The GSI library logs a 403 "The given origin is not allowed for the given
 * client ID" (GSI_LOGGER) when the current origin is not in the OAuth
 * client's Authorized JavaScript origins. That is a *configuration* error,
 * not a code bug — the button cannot work until Google Cloud Console is
 * fixed. This component suppresses the console spam and shows a friendly
 * dev message instead.
 */
export function GoogleButton({ label, mode = 'signin', onSuccess, intent = 'login', onLinkSuccess }: GoogleButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [originBlocked, setOriginBlocked] = useState(false);
  const fallbackRef = useRef<HTMLDivElement | null>(null);
  const inlineRef = useRef<HTMLDivElement | null>(null);
  const exchangingRef = useRef(false);

  const configured = isGoogleConfigured();

  const markOriginBlocked = useCallback(() => {
    setOriginBlocked(true);
    setError(
      'Google sign-in is not available on this origin. Add ' +
        window.location.origin +
        ' to Authorized JavaScript origins in Google Cloud Console ' +
        '(APIs & Services → Credentials → OAuth 2.0 Client ID). ' +
        'Required for dev: http://localhost:5173 and http://localhost:5174. ' +
        'Or run the backend without Google (email/password still works).',
    );
  }, []);

  const exchange = useCallback(
    async (credential: string) => {
      if (exchangingRef.current) return; // single-flight — GIS may re-emit
      exchangingRef.current = true;
      setBusy(true);
      setError(undefined);
      try {
        if (intent === 'link') {
          await api.post('/api/v1/me/identities/google', { id_token: credential });
          onLinkSuccess?.();
          setBusy(false);
          exchangingRef.current = false;
          return;
        }
        const res = await api.post<{
          access_token: string;
          refresh_token: string;
          needs_profile?: boolean;
          user: { email: string; name: string; avatar_url?: string | null; role?: string };
        }>('/api/v1/auth/oauth/google', { id_token: credential });
        setToken(res.access_token);
        try {
          if (res.refresh_token) localStorage.setItem('demandiq_refresh_token', res.refresh_token);
          else localStorage.removeItem('demandiq_refresh_token');
        } catch {}
        onSuccess?.({
          email: res.user.email,
          name: res.user.name,
          picture: res.user.avatar_url ?? undefined,
          role: res.user.role,
          needsProfile: res.needs_profile === true,
        });
      } catch (e) {
        setError(e instanceof ApiException ? e.error.message : 'Google sign-in failed. Please try again.');
        exchangingRef.current = false; // allow a retry
      } finally {
        setBusy(false);
      }
    },
    [onSuccess, intent, onLinkSuccess]);

  useEffect(
    () => onGoogleCredential((credential) => void exchange(credential)),
    [exchange]);

  // Suppress the noisy GSI 403 "origin not allowed" console spam on localhost
  // and surface a friendly message instead. The GSI library logs via
  // console.error / console.warn / window.onerror / unhandledrejection with
  // `GSI_LOGGER` and also via the blocked iframe's 403. Patching here prevents
  // the dev console spam seen in chunk-M2VDNRDH.js:88 and elsewhere.
  useEffect(() => {
    const handleBlocked = () => {
      // de-duplicate: only set once per mount
      setOriginBlocked((prev) => {
        if (!prev) {
          // use queueMicrotask so setError runs after the state batch
          queueMicrotask(markOriginBlocked);
        }
        return true;
      });
    };

    const onWindowError = (e: ErrorEvent) => {
      const msg = String((e as unknown as { message?: unknown }).message ?? (e.error as { message?: unknown } | undefined)?.message ?? e.message ?? '');
      // also catch the 403 URL that the iframe logs
      const src = String((e.target as HTMLIFrameElement | null)?.src ?? '');
      if (isGsiOriginBlockedMessage(msg) || isGsiOriginBlockedMessage(src)) {
        e.preventDefault();
        handleBlocked();
        return false;
      }
      return undefined;
    };

    const onUnhandledRejection = (e: PromiseRejectionEvent) => {
      const msg = String((e.reason as unknown as { message?: unknown })?.message ?? e.reason ?? '');
      if (isGsiOriginBlockedMessage(msg)) {
        e.preventDefault();
        handleBlocked();
      }
    };

    const stringifyArgs = (args: unknown[]): string =>
      args.map((a) => {
        if (typeof a === 'string') return a;
        try {
          return String((a as { message?: unknown })?.message ?? a);
        } catch {
          return String(a);
        }
      }).join(' ');

    const origError = console.error;
    const origWarn = console.warn;

    const patchedError = (...args: unknown[]) => {
      if (isGsiOriginBlockedMessage(stringifyArgs(args))) {
        handleBlocked();
        return;
      }
      (origError as (...a: unknown[]) => void)(...args);
    };

    const patchedWarn = (...args: unknown[]) => {
      if (isGsiOriginBlockedMessage(stringifyArgs(args))) {
        handleBlocked();
        return;
      }
      (origWarn as (...a: unknown[]) => void)(...args);
    };

    // Capture phase so we see the error before it bubbles to dev overlay
    window.addEventListener('error', onWindowError, true);
    window.addEventListener('unhandledrejection', onUnhandledRejection);
    (console as unknown as { error: (...a: unknown[]) => void }).error = patchedError;
    (console as unknown as { warn: (...a: unknown[]) => void }).warn = patchedWarn;

    return () => {
      window.removeEventListener('error', onWindowError, true);
      window.removeEventListener('unhandledrejection', onUnhandledRejection);
      (console as unknown as { error: (...a: unknown[]) => void }).error = origError;
      (console as unknown as { warn: (...a: unknown[]) => void }).warn = origWarn;
    };
  }, [markOriginBlocked]);

  useEffect(() => {
    // Not configured: show disabled state via render, don't attempt GSI load.
    if (!configured) return;
    if (originBlocked) return;
    const target = inlineRef.current;
    if (!target) return;
    // Clear any previous button (e.g. Fast Refresh re-render)
    target.innerHTML = '';
    let cancelled = false;
    void renderGoogleButton(target, {
      text: mode === 'signup' ? 'signup_with' : 'signin_with',
      width: 320,
    }).catch((err) => {
      if (cancelled || originBlocked) return;
      const msg = String((err as { message?: unknown })?.message ?? err ?? '');
      if (isGsiOriginBlockedMessage(msg)) {
        markOriginBlocked();
        return;
      }
      setError('Could not load the Google account chooser.');
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, originBlocked, configured]);

  // Not configured at build time — explain rather than silently hiding
  if (!configured && !originBlocked) {
    // Still render the disabled fallback so layout doesn't jump, but with
    // the "not configured" message exposed as `error`.
    // We set the error lazily to avoid setting state during render.
  }

  useEffect(() => {
    if (!configured && !originBlocked) {
      setError(
        'Google sign-in is not configured for this deployment (VITE_GOOGLE_CLIENT_ID missing). Email/password sign-in still works.',
      );
    }
  }, [configured, originBlocked]);

  const showDisabled = originBlocked || !configured;

  return (
    <div>
      {!showDisabled ? (
        <div className="flex justify-center">
          <div ref={inlineRef} aria-busy={busy} />
        </div>
      ) : (
        <div className="flex justify-center">
          <button
            type="button"
            disabled
            aria-disabled="true"
            title={
              originBlocked
                ? `Add ${typeof window !== 'undefined' ? window.location.origin : 'this origin'} to Authorized JavaScript origins in Google Cloud Console`
                : 'VITE_GOOGLE_CLIENT_ID missing'
            }
            className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-5 py-2.5 text-body-xs font-medium text-slate-500 disabled:cursor-not-allowed"
          >
            <GoogleMark />
            {originBlocked ? `${label} (unavailable on this origin)` : `${label} (not configured)`}
          </button>
        </div>
      )}

      <div ref={fallbackRef} className="mt-2 flex justify-center" />

      {error && (
        <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-micro leading-relaxed text-amber-800">
          {error}
        </p>
      )}
    </div>);

}

// ---- Profile completion (fresh Google sign-ups) ----------------------------

export interface CompleteProfilePayload {
  name: string;
  job_title?: string;
  phone?: string;
}

export function CompleteProfileForm({ onDone, embedded, initialName = '' }: {
  onDone: (account: { name: string; job_title?: string; phone?: string }) => void;
  embedded?: boolean;
  initialName?: string;
}) {
  const { toast } = useToast();
  const [name, setName] = useState(initialName);
  const [jobTitle, setJobTitle] = useState('');
  const [phone, setPhone] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || name.trim().length < 2) {
      setError('Enter your full name.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.post('/api/v1/me/profile/complete', {
        name: name.trim(),
        job_title: jobTitle.trim() || undefined,
        phone: phone.trim() || undefined,
      });
      toast('Profile completed', 'success');
      onDone({ name: name.trim(), job_title: jobTitle.trim() || undefined, phone: phone.trim() || undefined });
    } catch (e) {
      setError(e instanceof ApiException ? e.error.message : 'Could not save your profile.');
    } finally {
      setBusy(false);
    }
  };

  const fieldClasses =
    'w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-body-sm text-slate-800 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30';

  return (
    <form onSubmit={submit} className={embedded ? 'flex flex-col gap-3' : 'flex flex-col gap-3'}>
      <label className="flex flex-col gap-1.5">
        <span className="text-caption font-semibold text-slate-600">Full name</span>
        <input
          id="profile-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Aline Umutoni"
          autoComplete="name"
          className={fieldClasses}
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-caption font-semibold text-slate-600">Job title</span>
        <input
          id="profile-job"
          type="text"
          value={jobTitle}
          onChange={(e) => setJobTitle(e.target.value)}
          placeholder="e.g. Operations manager"
          autoComplete="organization-title"
          className={fieldClasses}
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-caption font-semibold text-slate-600">Phone (optional)</span>
        <input
          id="profile-phone"
          type="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+250 7XX XXX XXX"
          autoComplete="tel"
          className={fieldClasses}
        />
      </label>
      {error && (
        <p role="alert" className="text-meta text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="inline-flex w-full items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60">

        {busy ? 'Saving…' : 'Complete profile'}
      </button>
    </form>
  );
}
