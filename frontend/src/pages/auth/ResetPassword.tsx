import React, { useState } from 'react';
import { MailIcon, ArrowLeftIcon, LockIcon, EyeIcon, EyeOffIcon } from 'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { AuthField } from '../../components/auth/AuthField';
import { MailKeyArt } from '../../components/illustrations/AuthArt';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';

interface ResetPasswordProps {
  onNavigateAuth: (view: string) => void;
  onEmailSubmit: (email: string) => void;
}

export function ResetPassword({ onNavigateAuth, onEmailSubmit }: ResetPasswordProps) {
  const { toast } = useToast();
  const [email, setEmail] = useState('');
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [generalError, setGeneralError] = useState<string | undefined>();
  const [reference, setReference] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);

  // optional reset-with-token mode: detect token in URL or localStorage
  const tokenFromUrl = (() => {
    try {
      const sp = new URLSearchParams(window.location.search);
      return sp.get('token') ?? '';
    } catch {
      return '';
    }
  })();
  const [isResetMode] = useState(Boolean(tokenFromUrl));
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPass, setShowPass] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFieldError(undefined);
    setGeneralError(undefined);
    setReference(undefined);
    setOffline(false);

    // handle token+password reset mode
    if (isResetMode) {
      if (newPassword.length < 8) {
        setFieldError('Use at least 8 characters.');
        return;
      }
      if (newPassword !== confirm) {
        setFieldError('Passwords do not match.');
        return;
      }
      setLoading(true);
      try {
        await api.post('/api/v1/auth/password/reset', { token: tokenFromUrl, password: newPassword });
        toast('Password has been reset. Sign in with your new password.', 'success');
        onNavigateAuth('signin');
      } catch (e) {
        if (e instanceof ApiException) {
          const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
          setGeneralError(msg);
          setReference(e.error.reference ?? undefined);
          if (e.error.field) setFieldError(e.error.message);
          toast(msg, 'error');
        } else {
          const isNetwork = !(e instanceof ApiException);
          const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
          if (isNetwork || baseEmpty) {
            setOffline(true);
            toast('Backend unreachable. Password reset simulated offline.', 'info');
            onNavigateAuth('signin');
            return;
          }
          const msg = e instanceof Error ? e.message : 'Reset failed';
          setGeneralError(msg);
          toast(msg, 'error');
        }
      } finally {
        setLoading(false);
      }
      return;
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setFieldError('Enter a valid email address.');
      return;
    }
    setLoading(true);
    try {
      await api.post('/api/v1/auth/password/forgot', { email: email.trim().toLowerCase() });
      onEmailSubmit(email.trim().toLowerCase());
      toast('If the email exists, a reset link has been sent', 'success');
      onNavigateAuth('check-email');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        // forgot is always 202 even for unknown email, so 422 validation etc
        if (e.status === 422 && e.error.field === 'email') {
          setFieldError(e.error.message);
        } else {
          setGeneralError(msg);
          setReference(e.error.reference ?? undefined);
        }
        toast(msg, 'error');
        // still navigate to check-email to avoid enumeration? keep same UX as backend (always 202)
        if (e.status !== 422) {
          onEmailSubmit(email.trim().toLowerCase());
          onNavigateAuth('check-email');
        }
      } else {
        const isNetwork = !(e instanceof ApiException);
        const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
        if (isNetwork || baseEmpty) {
          setOffline(true);
          // offline fallback — still show check-email demo
          onEmailSubmit(email.trim().toLowerCase());
          toast('Backend unreachable. Showing demo check-email page.', 'info');
          onNavigateAuth('check-email');
          return;
        }
        const msg = e instanceof Error ? e.message : 'Request failed';
        setGeneralError(msg);
        toast(msg, 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      art={<MailKeyArt className="h-auto w-[300px]" />}
      artTitle="Back into your account in a minute"
      artText="We'll email a secure, single-use link. It expires in 30 minutes and never changes your data.">
      
      <h1 className="text-display-xs font-bold leading-tight tracking-tight text-slate-900">
        {isResetMode ? 'Set a new password' : 'Reset your password'}
      </h1>
      <p className="mt-1 text-body-xs text-slate-500">
        {isResetMode ? 'Enter your new password below.' : 'Enter your email and we\'ll send you a reset link.'}
      </p>

      <form className="mt-5 flex flex-col gap-3" onSubmit={handleSubmit} noValidate>
        {!isResetMode ? (
          <AuthField
            id="reset-email"
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            placeholder="Enter your email"
            icon={MailIcon}
            autoComplete="email"
            error={fieldError} />
        ) : (
          <>
            <AuthField
              id="reset-password"
              label="New password"
              type={showPass ? 'text' : 'password'}
              value={newPassword}
              onChange={setNewPassword}
              placeholder="At least 8 characters"
              icon={LockIcon}
              autoComplete="new-password"
              error={fieldError}
              trailing={
                <button
                  type="button"
                  onClick={() => setShowPass((v) => !v)}
                  className="mr-2.5 shrink-0 rounded p-0.5 text-slate-500 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  {showPass ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
                </button>
              }
            />
            <AuthField
              id="reset-confirm"
              label="Confirm password"
              type={showPass ? 'text' : 'password'}
              value={confirm}
              onChange={setConfirm}
              placeholder="Re-enter your password"
              icon={LockIcon}
              autoComplete="new-password"
              error={fieldError?.includes('match') ? fieldError : undefined}
            />
          </>
        )}

        {generalError && !offline && <EmptyState variant="error" size="sm" title={isResetMode ? 'Reset failed' : 'Request failed'} description={generalError} hints={reference ? [`Reference: ${reference}`] : undefined} />}
        {offline && <EmptyState variant="offline" size="sm" title="Backend unreachable" description={generalError ?? 'Proceeding offline (demo flow)'} hints={['VITE_API_URL not reachable, showing demo page']} />}

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60">
          
          {loading ? 'Sending…' : isResetMode ? 'Reset password' : 'Send Reset Link'}
        </button>
      </form>

      <button
        type="button"
        onClick={() => onNavigateAuth('signin')}
        className="mx-auto mt-3.5 flex items-center gap-1.5 rounded text-meta font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        <ArrowLeftIcon className="h-3.5 w-3.5" strokeWidth={2} />
        Back to Sign In
      </button>

      <MailKeyArt className="mx-auto mt-6 h-auto w-[210px] text-brand-300 md:hidden" />
    </AuthLayout>);

}
