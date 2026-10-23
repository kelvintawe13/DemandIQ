import React, { useState } from 'react';
import {
  MailIcon,
  LockIcon,
  EyeIcon,
  EyeOffIcon,
  ArrowRightIcon,
  AlertTriangleIcon } from
'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { AuthField } from '../../components/auth/AuthField';
import { TurnstileWidget } from '../../components/auth/TurnstileWidget';
import { isTurnstileEnabled } from '../../lib/turnstile';
import { GoogleButton } from '../../components/auth/GoogleButton';
import { isGoogleConfigured } from '../../lib/google';
import { Rise } from '../../components/ui/Rise';
import { DashboardArt } from '../../components/illustrations/AuthArt';
import { demoAccounts, findDemoAccount } from '../../data/demoAccount';
import type { DemoAccount } from '../../data/demoAccount';
import { api, ApiException, setToken } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { useSession, mapMeToSessionUser } from '../../contexts/SessionContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { AnimatedTitle } from '../../components/ui/AnimatedTitle';

interface SignInProps {
  onSignIn: (account: DemoAccount) => void;
  onChallenge: (account: DemoAccount) => void;
  onNavigateAuth: (view: string) => void;
  /** Shown as a Bootstrap-style alert, e.g. after a session-expiry redirect. */
  sessionNotice?: string;
  /** Fresh Google sign-up that must complete its profile first. */
  onCompleteProfile: (account: DemoAccount) => void;
}

export function SignIn({
  onSignIn,
  onChallenge,
  onNavigateAuth,
  sessionNotice,
  onCompleteProfile,
}: SignInProps) {
  const { toast } = useToast();
  const { setUser, refresh } = useSession();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [captchaToken, setCaptchaToken] = useState<string>();
  const [captchaError, setCaptchaError] = useState<string>();
  const [generalError, setGeneralError] = useState<string | undefined>();
  const [reference, setReference] = useState<string | undefined>();
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);

  const handleGoogleSuccess = async (account: {
    email: string;
    name: string;
    role?: string;
    needsProfile?: boolean;
  }) => {
    // Fresh Google sign-up: the backend holds the profile gate, so route to
    // completion before any app screen is reachable.
    if (account.needsProfile) {
      const initials =
        account.name.trim().split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'U';
      onCompleteProfile({
        id: 'clerk',
        email: account.email,
        password: '',
        name: account.name,
        role: 'Data Clerk',
        initials,
        label: 'New account',
        blurb: 'Profile incomplete',
        home: 'overview',
      });
      return;
    }
    // Token exchange is done; hydrate the session from /me (authoritative).
    try {
      const me = await api.get<{ user: { id: string; workspace_id: string; name: string; email: string; role: string }; workspace?: unknown; permissions: string[] }>('/api/v1/auth/me');
      setUser(mapMeToSessionUser(me as any));
    } catch {
      // token is stored; session will hydrate on next mount
    }
    // Only a platform administrator lands in the console seat; owners and
    // everyone else get the workspace seat.
    const roleRaw = account.role ?? 'operator';
    const isAdmin = roleRaw.toLowerCase() === 'administrator';
    const initials = account.name.trim().split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'U';
    onSignIn({
      id: isAdmin ? 'admin' : 'clerk',
      email: account.email,
      password: '',
      name: account.name,
      role: isAdmin ? 'Administrator' : 'Data Clerk',
      initials,
      label: isAdmin ? 'Admin (Google)' : 'Data Clerk (Google)',
      blurb: 'Signed in with Google',
      home: isAdmin ? 'admin-models' : 'overview',
    });
  };

  const signIn = async (loginEmail: string, loginPassword: string) => {
    setGeneralError(undefined);
    setReference(undefined);
    setFieldErrors({});
    setOffline(false);

    // basic client validation first; don't gate on captcha when fields are empty
    const fieldErrs: { email?: string; password?: string } = {};
    if (!loginEmail.trim()) fieldErrs.email = 'Enter your email.';
    if (!loginPassword) fieldErrs.password = 'Enter your password.';
    if (Object.keys(fieldErrs).length) {
      setFieldErrors(fieldErrs);
      return;
    }

    if (isTurnstileEnabled() && !captchaToken) {
      setCaptchaError('Complete the human check to continue.');
      return;
    }
    setCaptchaError(undefined);

    setLoading(true);
    try {
      const res = await api.post<{
        access_token?: string;
        refresh_token?: string;
        token_type?: string;
        user?: { id: string; workspace_id: string; name: string; email: string; role: string };
        mfa_required?: boolean;
        challenge_id?: string;
        needs_profile?: boolean;
      }>('/api/v1/auth/login', {
        email: loginEmail.trim(),
        password: loginPassword,
        turnstile_token: captchaToken,
      });

      // MFA challenge case
      if ((res as any).mfa_required && (res as any).challenge_id) {
        const challengeId = (res as any).challenge_id as string;
        try {
          localStorage.setItem('demandiq_mfa_challenge', challengeId);
          localStorage.setItem('demandiq_mfa_email', loginEmail.trim().toLowerCase());
        } catch {}
        toast('Two-factor required. Enter your code.', 'info');
        const demo = findDemoAccount(loginEmail, loginPassword) ?? ({
          id: 'admin',
          email: loginEmail.trim().toLowerCase(),
          password: loginPassword,
          name: loginEmail.split('@')[0],
          role: 'Administrator',
          initials: loginEmail.slice(0, 2).toUpperCase(),
          label: 'MFA demo',
          blurb: 'Requires second factor',
          home: 'admin-models',
        } as DemoAccount);
        onChallenge(demo);
        return;
      }

      if (res.access_token) {
        setToken(res.access_token);
        try {
          if (res.refresh_token) localStorage.setItem('demandiq_refresh_token', res.refresh_token);
          if (!res.refresh_token) localStorage.removeItem('demandiq_refresh_token');
        } catch {}
        // A gated (fresh Google) account must finish onboarding before the app.
        if (res.needs_profile) {
          onCompleteProfile?.({
            id: 'clerk',
            email: res.user?.email ?? loginEmail,
            password: '',
            name: res.user?.name ?? '',
            role: 'Data Clerk',
            initials: (res.user?.name ?? 'U').trim().split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'U',
            label: 'New account',
            blurb: 'Profile incomplete',
            home: 'overview',
          });
          return;
        }
        // hydrate session from /auth/me (authoritative)
        try {
          const me = await api.get<{ user: { id: string; workspace_id: string; name: string; email: string; role: string }; workspace?: unknown; permissions: string[] }>('/api/v1/auth/me');
          const mapped = mapMeToSessionUser(me as any);
          setUser(mapped);
        } catch {
          // fallback to user in login response if /me fails
          if (res.user) {
            const mapped = mapMeToSessionUser({ user: res.user as any, permissions: [] } as any);
            setUser(mapped);
          } else {
            await refresh().catch(() => undefined);
          }
        }
        // also store remember preference (no backend effect yet)
        try {
          localStorage.setItem('demandiq_remember', remember ? '1' : '0');
        } catch {}
        toast('Signed in successfully', 'success');

        // pick DemoAccount for App routing (keeps existing navigation contract)
        const roleRaw = (res.user?.role as string | undefined) ?? 'operator';
        const isAdmin = ['owner', 'administrator'].includes(roleRaw.toLowerCase());
        const found = findDemoAccount(loginEmail, loginPassword);
        let account: DemoAccount;
        if (found) {
          account = found;
        } else {
          const name = res.user?.name ?? loginEmail.split('@')[0];
          const initials = name
            .trim()
            .split(/\s+/)
            .map((p: string) => p[0])
            .join('')
            .slice(0, 2)
            .toUpperCase() || 'U';
          account = {
            id: isAdmin ? 'admin' : 'clerk',
            email: loginEmail.trim().toLowerCase(),
            password: loginPassword,
            name,
            role: isAdmin ? 'Administrator' : 'Data Clerk',
            initials,
            label: isAdmin ? 'Admin demo' : 'Data Clerk demo',
            blurb: '',
            home: isAdmin ? 'admin-models' : 'overview',
          };
        }
        // preserve admin MFA path already handled; operators go straight
        if (isAdmin && (res as any).mfa_required) {
          onChallenge(account);
        } else {
          onSignIn(account);
        }
        return;
      }

      setGeneralError('Unexpected response from server');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setGeneralError(msg);
        setReference(e.error.reference ?? undefined);
        const field = e.error.field as string | null | undefined;
        if (field === 'email') setFieldErrors({ email: e.error.message });
        else if (field === 'password') setFieldErrors({ password: e.error.message });
        else if (field) setFieldErrors({ [field]: e.error.message } as any);
        toast(msg, 'error');
      } else {
        // Network / offline — surface it plainly; sign-in has no demo
        // fallback anymore (real accounts only).
        const msg = e instanceof Error ? e.message : 'Network error: backend unreachable';
        setGeneralError(msg);
        setOffline(true);
        toast(msg, 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    void signIn(email, password);
  };

  const adminSeat = demoAccounts.find((account) => account.id === 'admin');

  const handleAdminQuickFill = () => {
    if (!adminSeat) return;
    setEmail(adminSeat.email);
    setPassword(adminSeat.password);
    setFieldErrors({});
    setGeneralError(undefined);
    setReference(undefined);
    // A captcha cannot be completed programmatically, so fill the fields and
    // let the person finish the check instead of sending a login that 401s.
    if (isTurnstileEnabled() && !captchaToken) {
      setCaptchaError('Complete the human check to continue.');
      return;
    }
    void signIn(adminSeat.email, adminSeat.password);
  };

  return (
    <AuthLayout
      art={<DashboardArt className="h-auto w-[300px]" />}
      artTitle="Forecast demand with confidence"
      artText="Ingest visitor data from spreadsheets, handwritten logs, or your POS. DemandIQ turns it into daily predictions.">
      
      <AnimatedTitle
        as="h1"
        variant="words"
        className="text-display-sm leading-tight tracking-tight text-slate-900"
      >
        Welcome back
      </AnimatedTitle>
      <p className="animate-subtitle-in mt-1.5 text-body-xs text-slate-500">
        Sign in to your account to continue
      </p>

      {sessionNotice &&
      <div
        role="alert"
        className="mt-3 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-meta leading-relaxed text-amber-800">

        <AlertTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
        <span className="min-w-0 flex-1">{sessionNotice}</span>
      </div>
      }

      <Rise delay={0.08}>
      <form className="mt-5 flex flex-col gap-3" onSubmit={handleSubmit} noValidate>
        <AuthField
          id="signin-email"
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="you@example.com"
          icon={MailIcon}
          autoComplete="email"
          error={fieldErrors.email} />
        

        <AuthField
          id="signin-password"
          label="Password"
          type={showPassword ? 'text' : 'password'}
          value={password}
          onChange={setPassword}
          placeholder="Enter your password"
          icon={LockIcon}
          autoComplete="current-password"
          error={fieldErrors.password}
          trailing={
          <button
            type="button"
            onClick={() => setShowPassword((value) => !value)}
            className="mr-2.5 flex shrink-0 items-center gap-1 rounded text-caption font-medium text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
            
              {showPassword ?
            <EyeOffIcon className="h-4 w-4" strokeWidth={1.75} /> :

            <EyeIcon className="h-4 w-4" strokeWidth={1.75} />
            }
              {showPassword ? 'Hide' : 'Show'}
            </button>
          } />
        

        <TurnstileWidget
          onVerify={(token) => {
            setCaptchaToken(token);
            setCaptchaError(undefined);
          }}
          onExpire={() => setCaptchaToken(undefined)}
          error={captchaError} />
        
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-meta text-slate-600">
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
            className="h-3.5 w-3.5 shrink-0 rounded border-slate-300 accent-brand-600" />
          
          Keep me signed in on this device
          </label>
        </div>

        {generalError && !offline &&
        <EmptyState
            variant="error"
            size="sm"
            title="Sign in failed"
            description={generalError}
            hints={reference ? [`Reference: ${reference}`] : undefined} />
        }
        {offline && generalError &&
        <EmptyState
            variant="offline"
            size="sm"
            title="Backend unreachable"
            description={generalError}
            hints={['Check VITE_API_URL points at the API', 'Start the backend at http://localhost:8000']} />
        }
        {offline && !generalError &&
        <EmptyState
            variant="offline"
            size="sm"
            title="Offline mode"
            description="Backend unreachable. Check your connection and try again."
            hints={['VITE_API_URL not reachable']} />
        }

        <button
          type="submit"
          disabled={loading}
          className="btn-rail inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60">
          
          {loading ? 'Signing in…' : 'Sign In'}
          {!loading && <ArrowRightIcon className="h-4 w-4" strokeWidth={2} />}
        </button>

        <div className="flex items-center justify-center gap-1.5 text-micro text-slate-500">
          <span>Quick fill</span>
          <button
            type="button"
            onClick={handleAdminQuickFill}
            disabled={loading}
            title="Sign in with the admin account"
            className="rounded-full border border-slate-200 bg-white px-2 py-0.5 font-semibold text-slate-700 transition-colors duration-150 ease-out hover:border-brand-300 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60">
            
            admin
          </button>
        </div>
      </form>
      </Rise>

      {isGoogleConfigured() && (
        <>
          <div className="my-3.5 flex items-center gap-3">
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-micro text-slate-500">or continue with</span>
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          <GoogleButton
            label="Sign in with Google"
            mode="signin"
            onSuccess={handleGoogleSuccess} />
        </>
      )}
      

      <button
        type="button"
        onClick={() => onNavigateAuth('reset')}
        className="mt-3 block w-full rounded text-center text-meta font-medium text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        Forgot password?
      </button>

      <p className="mt-4 text-center text-meta text-slate-600">
        Don&apos;t have an account?{' '}
        <button
          type="button"
          onClick={() => onNavigateAuth('signup')}
          className="rounded font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          Sign up
        </button>
      </p>
    </AuthLayout>);

}
