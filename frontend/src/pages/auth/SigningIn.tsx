import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { CheckIcon } from 'lucide-react';
import { api } from '../../api/client';
import { useSession } from '../../contexts/SessionContext';

interface SigningInProps {
  onComplete: () => void;
  userName: string;
}

/**
 * The steps this screen reports are real reads, not a scripted animation. Each
 * one runs the request the app would issue anyway on first paint, and the list
 * advances when that request finishes (success or failure — a failed read must
 * never strand the user here). A short floor keeps the screen legible when the
 * backend answers instantly; it is not a per-step delay.
 *
 * Boot runs exactly once. Callbacks and permission flags are read from refs so
 * a session hydrate (which re-renders App and would otherwise mint a new
 * `onComplete` identity) cannot cancel mid-flight and restart forever.
 */
const MIN_VISIBLE_MS = 650;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function SigningIn({ onComplete, userName }: SigningInProps) {
  const { user, refresh } = useSession();
  const onCompleteRef = useRef(onComplete);
  const refreshRef = useRef(refresh);
  onCompleteRef.current = onComplete;
  refreshRef.current = refresh;

  const steps = [
    { id: 'session', label: 'Confirming your session' },
    { id: 'workspace', label: 'Loading your workspace' },
    { id: 'alerts', label: 'Checking for new alerts' },
  ] as const;

  // Number of steps finished. The active step is `current` while it is running.
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();

    (async () => {
      // 1) Confirm session — refresh returns the hydrated user so we can read
      // permissions without waiting for a React re-render (which used to
      // restart this effect via an unstable onComplete dep).
      setCurrent(0);
      const hydrated = await refreshRef.current().catch(() => null);
      if (cancelled) return;
      setCurrent(1);

      const permissions = hydrated?.permissions ?? user.permissions ?? [];

      // 2) Workspace — best-effort; never strand the user on a failed read.
      if (permissions.includes('workspace:read')) {
        await api.get('/api/v1/workspace').catch(() => undefined);
      }
      if (cancelled) return;
      setCurrent(2);

      // 3) Alerts badge seed
      if (permissions.includes('alerts:read')) {
        await api.get('/api/v1/alerts', { status: 'unread', limit: 1 }).catch(() => undefined);
      }
      if (cancelled) return;
      setCurrent(3);

      const remaining = MIN_VISIBLE_MS - (Date.now() - started);
      if (remaining > 0) await sleep(remaining);
      if (!cancelled) onCompleteRef.current();
    })();

    return () => {
      cancelled = true;
    };
    // Mount-once: refs keep callbacks/session current without re-running boot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const progress = Math.min(100, Math.round(current / steps.length * 100));
  const radius = 46;
  const circumference = 2 * Math.PI * radius;

  return (
    <div className="app-viewport relative flex w-full items-center justify-center overflow-hidden bg-brand-900 px-5">
      {/* depth layers */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-25"
        style={{
          backgroundImage: 'radial-gradient(circle, #6fd6c6 1px, transparent 1px)',
          backgroundSize: '26px 26px'
        }} />
      
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -left-40 top-1/3 h-[420px] w-[420px] rounded-full bg-brand-800" />
      
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-32 -top-24 h-[360px] w-[360px] rounded-full bg-brand-800" />
      

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
        role="status"
        aria-live="polite"
        className="relative flex w-full max-w-[380px] flex-col items-center text-center">
        
        {/* Progress ring with brand mark */}
        <div className="relative h-[116px] w-[116px]">
          <svg viewBox="0 0 108 108" className="h-full w-full -rotate-90">
            <circle
              cx="54"
              cy="54"
              r={radius}
              fill="none"
              stroke="rgba(255,255,255,0.14)"
              strokeWidth="4" />
            
            <motion.circle
              cx="54"
              cy="54"
              r={radius}
              fill="none"
              stroke="#6fd6c6"
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray={circumference}
              initial={{ strokeDashoffset: circumference }}
              animate={{
                strokeDashoffset: circumference - circumference * Math.max(progress, 8) / 100
              }}
              transition={{ duration: 0.45, ease: [0.23, 1, 0.32, 1] }} />
            
          </svg>

          <div className="absolute inset-0 flex items-center justify-center">
            <svg viewBox="0 0 24 24" className="h-9 w-9" aria-hidden="true" fill="none">
              <rect x="2" y="13" width="4.4" height="9" rx="1.4" fill="#6fd6c6" />
              <rect x="8.8" y="8" width="4.4" height="14" rx="1.4" fill="#38bdaa" />
              <rect x="15.6" y="4" width="4.4" height="18" rx="1.4" fill="#ffffff" />
            </svg>
          </div>
        </div>

        <h1 className="mt-6 font-display text-h2 tracking-tight text-white">
          Welcome back,{' '}
          <span className="font-display-italic">{userName.split(' ')[0]}</span>
        </h1>
        <p className="mt-1.5 text-body-xs text-brand-100/80">
          Preparing your demand workspace · {progress}%
        </p>

        {/* Steps */}
        <ul className="mt-6 flex w-full flex-col gap-2 rounded-xl border border-white/10 bg-white/5 p-3 text-left">
          {steps.map((step, index) => {
            const isDone = index < current;
            const isActive = index === current;
            return (
              <li key={step.id} className="flex items-center gap-2.5">
                <span
                  className={[
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-full transition-colors duration-150 ease-out',
                  isDone ?
                  'bg-brand-300 text-brand-900' :
                  isActive ?
                  'border border-brand-300/60 bg-transparent' :
                  'border border-white/15 bg-transparent'].
                  join(' ')}>
                  
                  {isDone ?
                  <CheckIcon className="h-3 w-3" strokeWidth={3.5} /> :
                  isActive ?
                  <motion.span
                    className="h-1.5 w-1.5 rounded-full bg-brand-200"
                    animate={{ opacity: [1, 0.25, 1] }}
                    transition={{ duration: 1.1, repeat: Infinity, ease: 'linear' }} /> :


                  <span className="h-1.5 w-1.5 rounded-full bg-white/25" />
                  }
                </span>
                <span
                  className={[
                  'text-meta transition-colors duration-150 ease-out',
                  isDone ?
                  'text-brand-100/70' :
                  isActive ?
                  'font-semibold text-white' :
                  'text-white/40'].
                  join(' ')}>
                  
                  {step.label}
                </span>
              </li>);

          })}
        </ul>

        <p className="mt-6 text-fine uppercase tracking-[0.18em] text-brand-100/50">
          DemandIQ · secure session
        </p>
      </motion.div>
    </div>);
}
