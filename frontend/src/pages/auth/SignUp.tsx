import React, { useState } from 'react';
import {
  UserIcon,
  MailIcon,
  LockIcon,
  EyeIcon,
  EyeOffIcon,
  StoreIcon,
  MapPinIcon,
  UsersIcon,
  ArrowRightIcon,
  ArrowLeftIcon } from
'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { AuthField } from '../../components/auth/AuthField';
import { useLegal } from '../../contexts/LegalContext';
import { AuthSelect } from '../../components/auth/AuthSelect';
import { GoogleButton } from '../../components/auth/GoogleButton';
import { TurnstileWidget } from '../../components/auth/TurnstileWidget';
import { isTurnstileEnabled } from '../../lib/turnstile';
import { KitchenGrowthArt } from '../../components/illustrations/AuthArt';
import {
  businessTypes,
  coverBands,
  dataSources,
  locationCounts,
  passwordScore } from
'../../data/signupOptions';
import { api, ApiException } from '../../api/client';
import type { DemoAccount } from '../../data/demoAccount';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';

interface SignUpProps {
  onNavigateAuth: (view: string) => void;
  /** Called with the signed-in account when Google sign-up completes. */
  onSignIn?: (account: DemoAccount) => void;
  /** Fresh Google sign-up that must complete its profile first. */
  onCompleteProfile?: (account: DemoAccount) => void;
}

interface FormErrors {
  name?: string;
  email?: string;
  password?: string;
  confirm?: string;
  business?: string;
  businessType?: string;
  locations?: string;
  sources?: string;
  terms?: string;
}

const strengthTone = ['bg-slate-200', 'bg-red-400', 'bg-amber-400', 'bg-brand-600'];

function mapLocationCount(v: string): number | undefined {
  if (!v) return undefined;
  if (v === '1') return 1;
  if (v === '2-5') return 3;
  if (v === '6-20') return 10;
  if (v === '20+') return 25;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : undefined;
}

function mapDailyCovers(v: string): number | undefined {
  if (!v) return undefined;
  if (v === '<50') return 30;
  if (v === '50-150') return 100;
  if (v === '150-400') return 250;
  if (v === '400+') return 600;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : undefined;
}

export function SignUp({ onNavigateAuth, onSignIn, onCompleteProfile }: SignUpProps) {
  const { openLegal } = useLegal();
  const { toast } = useToast();
  const [step, setStep] = useState<1 | 2>(1);

  // Step 1 — who is signing up
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  // Step 2 — what we need to configure their workspace
  const [business, setBusiness] = useState('');
  const [businessType, setBusinessType] = useState('');
  const [locations, setLocations] = useState('');
  const [covers, setCovers] = useState('');
  const [sources, setSources] = useState<string[]>([]);
  const [agreed, setAgreed] = useState(false);
  const [captchaToken, setCaptchaToken] = useState<string>();
  const [captchaError, setCaptchaError] = useState<string>();

  const [errors, setErrors] = useState<FormErrors>({});
  const [generalError, setGeneralError] = useState<string | undefined>();
  const [reference, setReference] = useState<string | undefined>();
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(false);

  const strength = passwordScore(password);

  const handleGoogleSuccess = (account: {
    email: string;
    name: string;
    role?: string;
    needsProfile?: boolean;
  }) => {
    if (account.needsProfile) {
      const initials =
        account.name.trim().split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'U';
      onCompleteProfile?.({
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
    toast('Signed in with Google', 'success');
    // Only a platform administrator lands in the console seat.
    const roleRaw = account.role ?? 'operator';
    const isAdmin = roleRaw.toLowerCase() === 'administrator';
    const initials = account.name.trim().split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'U';
    onSignIn?.({
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

  const toggleSource = (id: string) => {
    setSources((current) =>
    current.includes(id) ?
    current.filter((item) => item !== id) :
    [...current.filter((item) => id === 'none' ? false : item !== 'none'), id]
    );
  };

  const submitStepOne = (event: React.FormEvent) => {
    event.preventDefault();
    const next: FormErrors = {};
    if (name.trim().length < 2) next.name = 'Enter your full name.';    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      next.email = 'Enter a valid work email address.';
    if (password.length < 8) next.password = 'Use at least 8 characters.';
    if (confirm !== password) next.confirm = 'Passwords do not match.';

    setErrors(next);
    if (Object.keys(next).length === 0) setStep(2);
  };

  const submitStepTwo = async (event: React.FormEvent) => {
    event.preventDefault();
    const next: FormErrors = {};
    if (business.trim().length < 2) next.business = 'Enter your business name.';
    if (!businessType) next.businessType = 'Select a business type.';
    if (!locations) next.locations = 'Select how many locations you run.';
    if (sources.length === 0) next.sources = 'Pick at least one. You can change it later.';
    if (!agreed) next.terms = 'Accept the terms to continue.';

    setErrors(next);
    setGeneralError(undefined);
    setReference(undefined);
    setOffline(false);
    // validate form fields before gating on captcha so all errors surface together
    if (Object.keys(next).length !== 0) return;
    if (isTurnstileEnabled() && !captchaToken) {
      setCaptchaError('Complete the human check to continue.');
      return;
    }
    setCaptchaError(undefined);

    setLoading(true);
    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        email: email.trim().toLowerCase(),
        password,
        business_name: business.trim(),
        business_type: businessType || undefined,
        location_count: mapLocationCount(locations),
        daily_covers: mapDailyCovers(covers),
        data_sources: sources,
        turnstile_token: captchaToken,
      };
      await api.post('/api/v1/auth/register', payload);
      try {
        localStorage.setItem('demandiq_pending_email', email.trim().toLowerCase());
      } catch {}
      toast('Account created. Check your email for a code.', 'success');
      onNavigateAuth('verify');
    } catch (e) {
      if (e instanceof ApiException) {
        const msg = e.reference ? `${e.error.message} (ref: ${e.error.reference})` : e.error.message;
        setGeneralError(msg);
        setReference(e.error.reference ?? undefined);
        const field = e.error.field as string | null | undefined;
        if (field) {
          // map backend field to frontend key
          const map: Record<string, keyof FormErrors> = {
            email: 'email',
            name: 'name',
            password: 'password',
            business_name: 'business',
            business_type: 'businessType',
            location_count: 'locations',
            data_sources: 'sources',
          };
          const key = map[field] ?? (field as keyof FormErrors);
          setErrors((prev) => ({ ...prev, [key]: e.error.message }));
        }
        toast(msg, 'error');
      } else {
        const isNetwork = !(e instanceof ApiException);
        const baseEmpty = !(import.meta.env.VITE_API_URL as string | undefined);
        if (isNetwork || baseEmpty) {
          // offline fallback — preserve demo behaviour: still advance to verify
          setOffline(true);
          try {
            localStorage.setItem('demandiq_pending_email', email.trim().toLowerCase());
          } catch {}
          toast('Backend unreachable. Proceeding offline (demo).', 'info');
          onNavigateAuth('verify');
          return;
        }
        const msg = e instanceof Error ? e.message : 'Registration failed';
        setGeneralError(msg);
        toast(msg, 'error');
      }
    } finally {
      setLoading(false);
    }
  };

  const eyeButton = (visible: boolean, toggle: () => void) =>
  <button
    type="button"
    aria-label={visible ? 'Hide password' : 'Show password'}
    onClick={toggle}
    className="mr-2.5 shrink-0 rounded p-0.5 text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
    
      {visible ?
    <EyeOffIcon className="h-4 w-4" strokeWidth={1.75} /> :

    <EyeIcon className="h-4 w-4" strokeWidth={1.75} />
    }
    </button>;


  return (
    <AuthLayout
      art={<KitchenGrowthArt className="h-auto w-[300px]" />}
      artTitle="Start forecasting, stop guessing"
      artText="Tell us how your kitchen records demand today and we will set the workspace up around it.">
      
      <div className="flex items-center gap-2">
        {[1, 2].map((index) =>
        <span
          key={index}
          className={[
          'h-1.5 flex-1 rounded-full transition-colors duration-200 ease-out',
          index <= step ? 'bg-brand-600' : 'bg-slate-200'].
          join(' ')} />

        )}
      </div>
      <p className="mt-2 text-caption font-medium text-slate-500">
        Step {step} of 2 · {step === 1 ? 'Your account' : 'Your business'}
      </p>

      <h1 className="mt-2 text-h2 text-slate-900">
        {step === 1 ? 'Create your account' : 'Tell us about your operation'}
      </h1>
      <p className="mt-1 text-body-sm text-slate-500">
        {step === 1 ?
        'You will be the workspace owner and can invite your team after setup.' :
        'This shapes your default locations, how your forecast is grouped, and which connectors we switch on.'}
      </p>

      {step === 1 &&
      <>
          <div className="mt-4">
            <GoogleButton
            label="Sign up with Google"
            mode="signup"
            onSuccess={handleGoogleSuccess} />
          
          </div>
          <div className="my-3.5 flex items-center gap-3">
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-caption text-slate-500">or use your email</span>
            <span className="h-px flex-1 bg-slate-200" />
          </div>
        </>
      }

      {step === 1 ?
      <form className="flex flex-col gap-3" onSubmit={submitStepOne} noValidate>
          <AuthField
          id="signup-name"
          label="Full name"
          value={name}
          onChange={setName}
          placeholder="Enter your full name"
          icon={UserIcon}
          autoComplete="name"
          error={errors.name} />
        
          <AuthField
          id="signup-email"
          label="Work email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="you@yourbusiness.com"
          icon={MailIcon}
          autoComplete="email"
          error={errors.email} />
        
          <div>
            <AuthField
            id="signup-password"
            label="Password"
            type={showPassword ? 'text' : 'password'}
            value={password}
            onChange={setPassword}
            placeholder="At least 8 characters"
            icon={LockIcon}
            autoComplete="new-password"
            error={errors.password}
            trailing={eyeButton(showPassword, () => setShowPassword((v) => !v))} />
          
            {password && !errors.password &&
          <div className="mt-1.5 flex items-center gap-2">
                <span className="flex flex-1 gap-1" aria-hidden="true">
                  {[1, 2, 3].map((bar) =>
              <span
                key={bar}
                className={[
                'h-1 flex-1 rounded-full transition-colors duration-150 ease-out',
                bar <= strength.score ? strengthTone[strength.score] : 'bg-slate-200'].
                join(' ')} />

              )}
                </span>
                <span className="text-caption font-medium text-slate-500">
                  {strength.label}
                </span>
              </div>
          }
          </div>
          <AuthField
          id="signup-confirm"
          label="Confirm password"
          type={showConfirm ? 'text' : 'password'}
          value={confirm}
          onChange={setConfirm}
          placeholder="Re-enter your password"
          icon={LockIcon}
          autoComplete="new-password"
          error={errors.confirm}
          trailing={eyeButton(showConfirm, () => setShowConfirm((v) => !v))} />
        

          <button
          type="submit"
          className="mt-1 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand-700 px-4 py-2.5 text-body-sm font-semibold text-white transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
          
            Continue
            <ArrowRightIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </form> :

      <form className="mt-5 flex flex-col gap-3" onSubmit={submitStepTwo} noValidate>
          <AuthField
          id="signup-business"
          label="Business name"
          value={business}
          onChange={setBusiness}
          placeholder="e.g. Taste Africa Kitchen"
          icon={StoreIcon}
          autoComplete="organization"
          error={errors.business} />
        
          <AuthSelect
          id="signup-type"
          label="Business type"
          value={businessType}
          onChange={setBusinessType}
          options={businessTypes}
          placeholder="Select a type"
          icon={StoreIcon}
          error={errors.businessType} />
        
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <AuthSelect
            id="signup-locations"
            label="Locations"
            value={locations}
            onChange={setLocations}
            options={locationCounts}
            placeholder="How many?"
            icon={MapPinIcon}
            error={errors.locations} />
          
            <AuthSelect
            id="signup-covers"
            label="Customers on a typical day"
            value={covers}
            onChange={setCovers}
            options={coverBands}
            placeholder="Optional"
            icon={UsersIcon} />
          
          </div>

          <fieldset>
            <legend className="text-body-xs font-semibold text-slate-800">
              How do you record demand today?
            </legend>
            <p className="mt-0.5 text-caption text-slate-500">
              Select all that apply. This decides which connectors we switch on.
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {dataSources.map((source) => {
              const selected = sources.includes(source.id);
              return (
                <button
                  key={source.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleSource(source.id)}
                  className={[
                  'rounded-full border px-3 py-1.5 text-caption font-medium transition-[border-color,background-color,color] duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                  selected ?
                  'border-brand-600 bg-brand-50 text-brand-800' :
                  'border-slate-300 bg-white text-slate-600 hover:border-slate-400'].
                  join(' ')}>
                  
                    {source.label}
                  </button>);

            })}
            </div>
            {errors.sources &&
          <p role="alert" className="mt-1 text-caption text-red-600">{errors.sources}</p>
          }
          </fieldset>

          <TurnstileWidget
          onVerify={(token) => {
            setCaptchaToken(token);
            setCaptchaError(undefined);
          }}
          onExpire={() => setCaptchaToken(undefined)}
          error={captchaError} />
        

          <div>
            <label className="flex items-start gap-2 text-body-sm text-slate-600">
              <input
              type="checkbox"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-slate-300 accent-brand-600" />
            
              <span>
                I agree to the{' '}
                <button
                type="button"
                onClick={() => openLegal('terms')}
                className="rounded font-medium text-brand-700 underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                
                  Terms of Service
                </button>{' '}
                and{' '}
                <button
                type="button"
                onClick={() => openLegal('privacy')}
                className="rounded font-medium text-brand-700 underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                
                  Privacy Policy
                </button>
              </span>
            </label>
            {errors.terms &&
          <p role="alert" className="mt-1 text-caption text-red-600">{errors.terms}</p>
          }
          </div>

          {generalError && !offline &&
          <EmptyState variant="error" size="sm" title="Registration failed" description={generalError} hints={reference ? [`Reference: ${reference}`] : undefined} />
          }
          {offline &&
          <EmptyState variant="offline" size="sm" title="Backend unreachable" description={generalError ?? 'Proceeding offline. Demo verification will accept any 6-digit code.'} hints={['VITE_API_URL not reachable, using offline demo path']} />
          }

          <div className="mt-1 flex gap-2">
            <button
            type="button"
            onClick={() => setStep(1)}
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-300 px-3.5 py-2.5 text-body-sm font-semibold text-slate-700 transition-[background-color,border-color,transform] duration-150 ease-out hover:bg-slate-50 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
            
              <ArrowLeftIcon className="h-4 w-4" strokeWidth={2} />
              Back
            </button>
            <button
            type="submit"
            disabled={loading}
            className="inline-flex flex-1 items-center justify-center rounded-xl bg-brand-700 px-4 py-2.5 text-body-sm font-semibold text-white transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100">
            
              {loading ? 'Creating…' : 'Create account'}
            </button>
          </div>
        </form>
      }

      <p className="mt-3.5 text-center text-body-sm text-slate-600">
        Already have an account?{' '}
        <button
          type="button"
          onClick={() => onNavigateAuth('signin')}
          className="rounded font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          Sign in
        </button>
      </p>
    </AuthLayout>);

}
