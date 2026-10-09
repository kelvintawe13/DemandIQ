import React, { useRef, useState } from 'react';
import { ArrowLeftIcon, SmartphoneIcon, KeyRoundIcon } from 'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { ShieldCodeArt } from '../../components/illustrations/AuthArt';
import { api, ApiException, setToken } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { useSession, mapMeToSessionUser } from '../../contexts/SessionContext';
import { EmptyState } from '../../components/ui/EmptyState';

interface TwoFactorProps {
  email: string;
  onVerified: () => void;
  onNavigateAuth: (view: string) => void;
}

export function TwoFactor({ email, onVerified, onNavigateAuth }: TwoFactorProps) {
  const { toast } = useToast();
  const { setUser, refresh } = useSession();
  const [digits, setDigits] = useState<string[]>(Array(6).fill(''));
  const [useRecovery, setUseRecovery] = useState(false);
  const [recovery, setRecovery] = useState('');
  const [trust, setTrust] = useState(true);
  const [error, setError] = useState<string>();
  const [reference, setReference] = useState<string | undefined>();
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const inputs = useRef<Array<HTMLInputElement | null>>([]);

  const setDigit = (index: number, value: string) => {
    const clean = value.replace(/\D/g, '').slice(-1);
    setDigits((current) => {
      const next = [...current];
      next[index] = clean;
      return next;
    });
    if (clean && index < 5) inputs.current[index + 1]?.focus();
  };

  const handleKeyDown = (index: number, event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Backspace' && !digits[index] && index > 0) {
      inputs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (event: React.ClipboardEvent) => {
    const pasted = event.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    if (!pasted) return;
    event.preventDefault();
    const next = Array(6).fill('');
    pasted.split('').forEach((char, index) => {
      next[index] = char;
    });
    setDigits(next);
    inputs.current[Math.min(pasted.length, 5)]?.focus();
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(undefined);
    setReference(undefined);
    setFieldError(undefined);
    setOffline(false);

    if (useRecovery) {
      if (recovery.trim().length < 8) {
        setFieldError('Enter one of your 10-character recovery codes.');
        return;
      }
    } else {
      if (digits.some((digit) => !digit)) {
        setFieldError('Enter all six digits from your authenticator app.');
        return;
      }
    }

    const code = useRecovery ? undefined : digits.join('');
    const recovery_code = useRecovery ? recovery.trim() : undefined;
    let challenge_id = '';
    try {
      challenge_id = localStorage.getItem('demandiq_mfa_challenge') ?? '';
    } catch {}
    if (!challenge_id) {
      setFieldError('Missing challenge. Please sign in again to get a new code.');
      setError('MFA challenge not found. Return to sign in and try again.');
      return;
    }
    // Validate challenge_id is UUID format before sending (backend will also validate)
    const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRe.test(challenge_id)) {
      setFieldError('Invalid challenge identifier.');
      setError('Invalid challenge format. Please sign in again.');
      return;
    }
    if (!useRecovery && code && !/^\d{6}$/.test(code)) {
      setFieldError('Enter a 6-digit numeric code.');
      return;
    }

    setLoading(true);
    try {
      const res = await api.post<{
        access_token: string;
        refresh_token: string;
        token_type: string;
        expires_in: number;
        user: { id: string; workspace_id: string; name: string; email: string; role: string };
      }>('/api/v1/auth/mfa/verify', {
        challenge_id,
        code,
        recovery_code,
        trust_device: trust,
      });

      if (!res.access_token || !res.refresh_token) {
        throw new Error('Invalid server response: missing tokens');
      }
      setToken(res.access_token);
      try {
        localStorage.setItem('demandiq_refresh_token', res.refresh_token);
      } catch {}
      try {
        const me = await api.get<{ user: { id: string; workspace_id: string; name: string; email: string; role: string }; permissions: string[] }>('/api/v1/auth/me');
        const mapped = mapMeToSessionUser(me as any);
        setUser(mapped);
      } catch {
        if (res.user) {
          const mapped = mapMeToSessionUser({ user: res.user as any, permissions: [] } as any);
          setUser(mapped);
        } else {
          await refresh().catch(() => undefined);
        }
      }
      try {
        localStorage.removeItem('demandiq_mfa_challenge');
      } catch {}
      toast('Two-factor verified', 'success');
      onVerified();
      return;
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setError(msg);
        setReference(e.error.reference ?? undefined);
        if (e.error.field === 'code' || e.error.field === 'recovery_code' || e.error.field === 'challenge_id') setFieldError(e.error.message);
        toast(msg, 'error');
      } else {
        const isNetwork = e instanceof TypeError || (e instanceof Error && /fetch|network|Failed to fetch/i.test(e.message));
        if (isNetwork) {
          setOffline(true);
          const msg = e instanceof Error ? e.message : 'Backend unreachable';
          setError(msg);
          toast(msg, 'error');
          return;
        }
        const msg = e instanceof Error ? e.message : 'Verification failed';
        setError(msg);
        toast(msg, 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      art={<ShieldCodeArt className="h-auto w-[300px]" />}
      artTitle="One more step"
      artText="Two-factor authentication keeps forecast data and connector credentials protected even if a password leaks.">
      
      <button
        type="button"
        onClick={() => onNavigateAuth('signin')}
        className="inline-flex items-center gap-1.5 rounded text-meta font-medium text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        <ArrowLeftIcon className="h-3.5 w-3.5" strokeWidth={2} />
        Back to sign in
      </button>

      <h1 className="mt-3 text-display-xs font-bold leading-tight tracking-tight text-slate-900">
        Two-factor authentication
      </h1>
      <p className="mt-1 text-body-xs leading-relaxed text-slate-500">
        {useRecovery ?
        'Enter a recovery code you saved when you set up two-factor.' :
        'Open your authenticator app and enter the 6-digit code for'}{' '}
        {!useRecovery && <span className="font-medium text-slate-700">{email}</span>}
      </p>

      <form className="mt-5" onSubmit={submit} noValidate>
        {useRecovery ?
        <div>
            <label
            htmlFor="recovery-code"
            className="block text-meta font-semibold text-slate-800">
            
              Recovery code
            </label>
            <div className="mt-1 flex items-center rounded-lg border border-slate-300 bg-white transition-colors duration-150 ease-out focus-within:border-brand-500 focus-within:ring-1 focus-within:ring-brand-500">
              <KeyRoundIcon
              className="ml-2.5 h-4 w-4 shrink-0 text-slate-500"
              strokeWidth={1.75} />
            
              <input
              id="recovery-code"
              value={recovery}
              onChange={(event) => setRecovery(event.target.value)}
              placeholder="XXXXX-XXXXX"
              required
              minLength={8}
              className="w-full min-w-0 bg-transparent px-2.5 py-2.5 font-mono text-body-sm tracking-wide text-slate-900 placeholder:text-slate-500 focus:outline-none" />
            
            </div>
            {fieldError && <p className="mt-1 text-micro text-red-600" role="alert">{fieldError}</p>}
          </div> :

        <div
          className="flex justify-between gap-1.5"
          onPaste={handlePaste}
          role="group"
          aria-label="Six digit authentication code">
          
            {digits.map((digit, index) =>
          <input
            key={index}
            ref={(element) => {
              inputs.current[index] = element;
            }}
            value={digit}
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={1}
            required
            autoComplete="one-time-code"
            aria-label={`Digit ${index + 1}`}
            onChange={(event) => setDigit(index, event.target.value)}
            onKeyDown={(event) => handleKeyDown(index, event)}
            className="h-12 w-full min-w-0 rounded-lg border border-slate-300 bg-white text-center text-h4 font-semibold text-slate-900 transition-colors duration-150 ease-out focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />

          )}
          </div>
        }

        {!useRecovery && fieldError && <p className="mt-2 text-micro text-red-600" role="alert">{fieldError}</p>}

        {error && !offline &&
        <div className="mt-3">
            <EmptyState variant="error" size="sm" title="Verification failed" description={error} hints={reference ? [`Reference: ${reference}`] : undefined} />
          </div>
        }
        {offline &&
        <div className="mt-3">
            <EmptyState variant="offline" size="sm" title="Offline mode" description="Backend unreachable. Accepting code offline." hints={['Any 6-digit code will succeed in demo offline mode']} />
          </div>
        }

        <label className="mt-3 flex items-start gap-2 text-meta text-slate-600">
          <input
            type="checkbox"
            checked={trust}
            onChange={(event) => setTrust(event.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-slate-300 accent-brand-600" />
          
          Trust this device for 30 days
        </label>

        <button
          type="submit"
          disabled={loading}
          className="mt-4 inline-flex w-full items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60">
          
          {loading ? 'Verifying…' : 'Verify and continue'}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-meta">
        <button
          type="button"
          onClick={() => {
            setUseRecovery((value) => !value);
            setError(undefined);
            setFieldError(undefined);
          }}
          className="rounded font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          {useRecovery ? 'Use authenticator app' : 'Use a recovery code'}
        </button>
        <span className="text-slate-300" aria-hidden="true">
          ·
        </span>
        <span className="inline-flex items-center gap-1.5 text-slate-500">
          <SmartphoneIcon className="h-3.5 w-3.5" strokeWidth={1.8} />
          Codes refresh every 30s
        </span>
      </div>
    </AuthLayout>);

}
