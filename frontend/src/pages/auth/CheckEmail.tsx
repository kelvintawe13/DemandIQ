import { useState } from 'react';
import { MailCheckIcon, ArrowLeftIcon } from 'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { MailKeyArt } from '../../components/illustrations/AuthArt';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';

interface CheckEmailProps {
  email: string;
  onNavigateAuth: (view: string) => void;
}

export function CheckEmail({ email, onNavigateAuth }: CheckEmailProps) {
  const { toast } = useToast();
  const [resent, setResent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [generalError, setGeneralError] = useState<string | undefined>();
  const [reference, setReference] = useState<string | undefined>();
  const [offline, setOffline] = useState(false);

  const handleResend = async () => {
    setGeneralError(undefined);
    setReference(undefined);
    setOffline(false);
    setLoading(true);
    try {
      // password reset flow — resend via /password/forgot; if this is verify flow we also try resend-verification
      // try forgot first
      await api.post('/api/v1/auth/password/forgot', { email: (email || '').trim().toLowerCase() });
      setResent(true);
      toast('Reset link sent again', 'success');
    } catch (e) {
      // fallback to resend-verification for verify flow
      if (e instanceof ApiException) {
        try {
          await api.post('/api/v1/auth/resend-verification', { email: (email || '').trim().toLowerCase(), code: '000000' });
          setResent(true);
          toast('Verification code resent', 'success');
          return;
        } catch (e2) {
          if (e2 instanceof ApiException) {
            const msg = e2.reference ? `${e2.error.message} (ref: ${e2.error.reference})` : e2.error.message;
            setGeneralError(msg);
            setReference(e2.error.reference ?? undefined);
            toast(msg, 'error');
          } else {
            const isNetwork = !(e2 instanceof ApiException);
            const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
            if (isNetwork || baseEmpty) {
              setOffline(true);
              setResent(true);
              toast('Backend unreachable. Resend simulated offline.', 'info');
              return;
            }
            const msg = e2 instanceof Error ? e2.message : 'Resend failed';
            setGeneralError(msg);
            toast(msg, 'error');
          }
          return;
        }
      }
      // network/offline
      const isNetwork = !(e instanceof ApiException);
      const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
      if (isNetwork || baseEmpty) {
        setOffline(true);
        setResent(true);
        toast('Backend unreachable. Resend simulated offline.', 'info');
        return;
      }
      const msg = e instanceof Error ? (e as Error).message : 'Resend failed';
      setGeneralError(msg);
      toast(msg, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      art={<MailKeyArt className="h-auto w-[300px]" />}
      artTitle="Check your inbox"
      artText="Reset links are single-use and expire after 30 minutes for your security.">
      
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-brand-50">
        <MailCheckIcon className="h-5 w-5 text-brand-600" strokeWidth={1.75} />
      </span>

      <h1 className="mt-4 text-display-xs font-bold leading-tight tracking-tight text-slate-900">
        Check your email
      </h1>
      <p className="mt-1.5 text-body-xs leading-relaxed text-slate-600">
        We sent a password reset link to{' '}
        <span className="font-semibold text-slate-900">
          {email || 'your email address'}
        </span>
        . The link expires in 30 minutes.
      </p>

      {generalError && !offline && <div className="mt-3"><EmptyState variant="error" size="sm" title="Resend failed" description={generalError} hints={reference ? [`Reference: ${reference}`] : undefined} /></div>}
      {offline && <div className="mt-3"><EmptyState variant="offline" size="sm" title="Backend unreachable" description={generalError ?? 'Resend simulated offline'} hints={['VITE_API_URL not reachable, using offline demo']} /></div>}

      <button
        type="button"
        disabled={loading}
        onClick={handleResend}
        className="mt-5 inline-flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60">
        
        {loading ? 'Sending…' : resent ? 'Reset link sent again' : 'Resend email'}
      </button>

      <p className="mt-2 text-micro text-slate-500" aria-live="polite">
        {resent ?
        'Sent. Check your spam folder if it still has not arrived.' :
        "Didn't get it? Check your spam folder first."}
      </p>

      <button
        type="button"
        onClick={() => onNavigateAuth('signin')}
        className="mt-4 flex items-center gap-1.5 rounded text-meta font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        <ArrowLeftIcon className="h-3.5 w-3.5" strokeWidth={2} />
        Back to Sign In
      </button>
    </AuthLayout>);

}
