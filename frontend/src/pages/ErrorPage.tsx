import {
  RefreshCwIcon,
  ArrowLeftIcon,
  HomeIcon,
  LifeBuoyIcon,
  CopyIcon } from
'lucide-react';
import { NotFoundArt, BrokenPlugArt, OfflineArt } from '../components/illustrations/StateArt';
import { BrandLogo } from '../components/auth/BrandLogo';

export type ErrorKind = 'not-found' | 'crash' | 'forbidden' | 'offline';

interface ErrorPageProps {
  kind?: ErrorKind;
  /** Technical detail shown in the diagnostics strip. Never shown to end users as prose. */
  detail?: string;
  onHome?: () => void;
  onBack?: () => void;
  onRetry?: () => void;
}

const copy: Record<
  ErrorKind,
  {
    code: string;
    title: string;
    body: string;
    art: (props: {className?: string;}) => JSX.Element;
    tone: string;
  }> =
{
  'not-found': {
    code: '404',
    title: 'We can’t find that screen',
    body: 'The page may have been renamed, or the link you followed is out of date. Everything else in your workspace is fine.',
    art: NotFoundArt,
    tone: 'text-brand-300'
  },
  crash: {
    code: '500',
    title: 'Something went wrong on our side',
    body: 'The screen failed to load. Your data is safe and nothing was lost. Retrying usually resolves it.',
    art: BrokenPlugArt,
    tone: 'text-red-300'
  },
  forbidden: {
    code: '403',
    title: 'You don’t have access to this',
    body: 'This area is restricted to another role. Ask a workspace administrator if you need it opened up.',
    art: BrokenPlugArt,
    tone: 'text-amber-300'
  },
  offline: {
    code: 'Offline',
    title: 'No connection right now',
    body: 'We can’t reach DemandIQ. Anything you record on this device is queued and syncs automatically when you’re back online.',
    art: OfflineArt,
    tone: 'text-amber-300'
  }
};

export function ErrorPage({
  kind = 'not-found',
  detail,
  onHome,
  onBack,
  onRetry
}: ErrorPageProps) {
  const { code, title, body, art: Art, tone } = copy[kind];
  const reference = `DQ-${code}-${new Date().toISOString().slice(0, 10)}`;

  return (
    <main className="flex min-h-screen w-full flex-col bg-slate-50 dark:bg-slate-950">
      <header className="flex items-center justify-between gap-3 px-4 py-4 sm:px-8">
        <BrandLogo />
        <button
          type="button"
          onClick={() => window.open('mailto:support@demandiq.app')}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-slate-700 dark:text-slate-200">
          
          <LifeBuoyIcon className="h-3.5 w-3.5" strokeWidth={2} />
          Contact support
        </button>
      </header>

      <div className="flex flex-1 items-center justify-center px-4 py-8 sm:px-8">
        <div className="grid w-full max-w-4xl grid-cols-1 items-center gap-8 md:grid-cols-2 md:gap-12">
          <div className="order-2 min-w-0 md:order-1">
            <span className="inline-flex items-center rounded-full bg-brand-100 px-2.5 py-1 text-micro font-semibold tracking-wide text-brand-800">
              {code}
            </span>
            <h1 className="mt-3 text-display-xs font-bold leading-tight tracking-tight text-slate-900 sm:text-display-sm dark:text-slate-50">
              {title}
            </h1>
            <p className="mt-2.5 max-w-[46ch] text-body-sm leading-relaxed text-slate-600 sm:text-body-sm-lg dark:text-slate-400">
              {body}
            </p>

            <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              {onRetry &&
              <button
                type="button"
                onClick={onRetry}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-body-sm font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
                
                  <RefreshCwIcon className="h-4 w-4" strokeWidth={2} />
                  Try again
                </button>
              }
              {onHome &&
              <button
                type="button"
                onClick={onHome}
                className={[
                'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-body-sm font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2',
                onRetry ?
                'border border-slate-300 text-slate-700 hover:bg-white dark:border-slate-700 dark:text-slate-200' :
                'bg-brand-700 text-white hover:bg-brand-800'].
                join(' ')}>
                
                  <HomeIcon className="h-4 w-4" strokeWidth={2} />
                  Back to dashboard
                </button>
              }
              {onBack &&
              <button
                type="button"
                onClick={onBack}
                className="inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-body-sm font-semibold text-slate-600 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-slate-300">
                
                  <ArrowLeftIcon className="h-4 w-4" strokeWidth={2} />
                  Go back
                </button>
              }
            </div>

            <div className="mt-7 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900">
              <span className="text-fine uppercase tracking-wide text-slate-500">
                Reference
              </span>
              <code className="min-w-0 flex-1 truncate font-mono text-micro text-slate-600 dark:text-slate-300">
                {detail ?? reference}
              </code>
              <button
                type="button"
                onClick={() => navigator.clipboard?.writeText(detail ?? reference)}
                aria-label="Copy error reference"
                className="shrink-0 rounded-md p-1 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:hover:bg-slate-800">
                
                <CopyIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
              </button>
            </div>
          </div>

          <div className="order-1 flex justify-center md:order-2">
            <div className="w-full max-w-[320px] rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_18px_44px_rgba(15,23,42,0.08)] sm:p-8 dark:border-slate-800 dark:bg-slate-900">
              <Art className={`h-auto w-full ${tone}`} />
            </div>
          </div>
        </div>
      </div>
    </main>);

}