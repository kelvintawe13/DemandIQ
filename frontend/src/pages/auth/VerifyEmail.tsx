import React, { useEffect, useRef, useState } from 'react';
import { ShieldCheckIcon, ArrowLeftIcon } from 'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { ShieldCodeArt } from '../../components/illustrations/AuthArt';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { useSiteMeta } from '../../contexts/SiteMetaContext';
import { EmptyState } from '../../components/ui/EmptyState';

interface VerifyEmailProps {
  onNavigateAuth: (view: string) => void;
}

const CODE_LENGTH = 6;

export function VerifyEmail({ onNavigateAuth }: VerifyEmailProps) {
  const { toast } = useToast();
  const { meta } = useSiteMeta();
  // Verification only exists when the backend can deliver a code (SMTP
  // configured). A direct hit on this screen otherwise goes back to sign-in.
  useEffect(() => {
    if (meta && !meta.email_verification_required) onNavigateAuth('signin');
  }, [meta, onNavigateAuth]);
  const [digits, setDigits] = useState<string[]>(Array(CODE_LENGTH).fill(''));
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [generalError, setGeneralError] = useState<string | undefined>();
  const [reference, setReference] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [resendLoading, setResendLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const inputsRef = useRef<Array<HTMLInputElement | null>>([]);

  const pendingEmail = (() => {
    try {
      return localStorage.getItem('demandiq_pending_email') ?? '';
    } catch {
      return '';
    }
  })();
  const [email] = useState(pendingEmail);

  const setDigit = (index: number, value: string) => {
    const clean = value.replace(/\D/g, '').slice(-1);
    setDigits((current) => {
      const next = [...current];
      next[index] = clean;
      return next;
    });
    if (clean && index < CODE_LENGTH - 1) {
      inputsRef.current[index + 1]?.focus();
    }
  };

  const handleKeyDown = (index: number, event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Backspace' && !digits[index] && index > 0) {
      inputsRef.current[index - 1]?.focus();
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFieldError(undefined);
    setGeneralError(undefined);
    setReference(undefined);
    setOffline(false);
    if (digits.some((digit) => digit === '')) {
      setFieldError('Enter all 6 digits of your verification code.');
      return;
    }
    const code = digits.join('');
    const targetEmail = email || (() => {
      try { return localStorage.getItem('demandiq_pending_email') ?? ''; } catch { return ''; }
    })();
    if (!targetEmail) {
      setFieldError('Missing email. Go back and sign up again.');
      return;
    }
    setLoading(true);
    try {
      await api.post('/api/v1/auth/verify-email', { email: targetEmail, code });
      toast('Email verified. You can now sign in.', 'success');
      try { localStorage.removeItem('demandiq_pending_email'); } catch {}
      onNavigateAuth('all-set');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setGeneralError(msg);
        setReference(e.error.reference ?? undefined);
        if (e.error.field === 'code') setFieldError(e.error.message);
        else if (e.error.field === 'email') setFieldError(e.error.message);
        toast(msg, 'error');
      } else {
        const isNetwork = !(e instanceof ApiException);
        const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
        if (isNetwork || baseEmpty) {
          // offline demo fallback — any 6 digits accepted
          setOffline(true);
          toast('Backend unreachable. Accepting code offline (demo).', 'info');
          onNavigateAuth('all-set');
          return;
        }
        const msg = e instanceof Error ? e.message : 'Verification failed';
        setGeneralError(msg);
        toast(msg, 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    const targetEmail = email || (() => { try { return localStorage.getItem('demandiq_pending_email') ?? ''; } catch { return ''; } })();
    if (!targetEmail) {
      toast('No email to resend to', 'error');
      return;
    }
    setResendLoading(true);
    setGeneralError(undefined);
    try {
      await api.post('/api/v1/auth/resend-verification', { email: targetEmail, code: '000000' });
      toast('Verification code resent', 'success');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setGeneralError(msg);
        setReference(e.error.reference ?? undefined);
        toast(msg, 'error');
      } else {
        const isNetwork = !(e instanceof ApiException);
        const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
        if (isNetwork || baseEmpty) {
          setOffline(true);
          toast('Backend unreachable. Resend simulated offline.', 'info');
          return;
        }
        const msg = e instanceof Error ? e.message : 'Resend failed';
        setGeneralError(msg);
        toast(msg, 'error');
      }
    } finally {
      setResendLoading(false);
    }
  };

  return (
    <AuthLayout
      art={<ShieldCodeArt className="h-auto w-[280px]" />}
      artTitle="One quick security step"
      artText="Verifying your email keeps your demand data and connected POS accounts protected.">
      
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-brand-50">
        <ShieldCheckIcon className="h-5 w-5 text-brand-600" strokeWidth={1.75} />
      </span>

      <h1 className="mt-4 text-display-xs font-bold leading-tight tracking-tight text-slate-900">
        Verify your email
      </h1>
      <p className="mt-1.5 text-body-xs leading-relaxed text-slate-600">
        Enter the 6-digit code we sent to <span className="font-medium text-slate-900">{email || 'your inbox'}</span>.
      </p>
      {/* The offline hint belongs on screen only when the backend is actually
          unreachable; showing it during a normal sign-up just confused people. */}
      {offline && (
        <p className="mt-1 text-caption text-amber-700">
          Offline demo: any 6 digits will succeed.
        </p>
      )}

      <form className="mt-5" onSubmit={handleSubmit} noValidate>
        <div className="flex gap-1.5 sm:gap-2">
          {digits.map((digit, index) =>
          <input
            key={index}
            ref={(element) => {
              inputsRef.current[index] = element;
            }}
            value={digit}
            onChange={(event) => setDigit(index, event.target.value)}
            onKeyDown={(event) => handleKeyDown(index, event)}
            inputMode="numeric"
            maxLength={1}
            aria-label={`Digit ${index + 1}`}
            className="h-11 w-full min-w-0 rounded-lg border border-slate-300 text-center text-lead font-semibold text-slate-900 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />

          )}
        </div>

        {fieldError &&
        <p className="mt-2 text-micro text-red-600" role="alert">
            {fieldError}
          </p>
        }
        {generalError && !offline &&
        <div className="mt-3">
            <EmptyState variant="error" size="sm" title="Verification failed" description={generalError} hints={reference ? [`Reference: ${reference}`] : undefined} />
          </div>
        }
        {offline &&
        <div className="mt-3">
            <EmptyState variant="offline" size="sm" title="Offline mode" description={generalError ?? 'Backend unreachable. Any code accepted offline.'} hints={['VITE_API_URL not reachable, using offline demo']} />
          </div>
        }

        <button
          type="submit"
          disabled={loading}
          className="mt-4 inline-flex w-full items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60">
          
          {loading ? 'Verifying…' : 'Verify Account'}
        </button>
      </form>

      <p className="mt-3 text-meta text-slate-600">
        Didn&apos;t receive a code?{' '}
        <button
          type="button"
          disabled={resendLoading}
          onClick={handleResend}
          className="rounded font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60">
          
          {resendLoading ? 'Sending…' : 'Resend code'}
        </button>
      </p>

      <button
        type="button"
        onClick={() => onNavigateAuth('signin')}
        className="mt-4 flex items-center gap-1.5 rounded text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        <ArrowLeftIcon className="h-3.5 w-3.5" strokeWidth={2} />
        Back to Sign In
      </button>
    </AuthLayout>);

}
