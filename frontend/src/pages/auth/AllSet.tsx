import { useEffect } from 'react';
import { HomeIcon, LoaderIcon } from 'lucide-react';
import { AuthLayout } from '../../components/auth/AuthLayout';
import { SuccessCheckArt } from '../../components/illustrations/AuthArt';

interface AllSetProps {
  onEnterApp: () => void;
}

export function AllSet({ onEnterApp }: AllSetProps) {
  useEffect(() => {
    const timeout = window.setTimeout(onEnterApp, 4000);
    return () => window.clearTimeout(timeout);
  }, [onEnterApp]);

  return (
    <AuthLayout
      art={<SuccessCheckArt className="h-auto w-[300px]" />}
      artTitle="Your workspace is ready"
      artText="Import your first demand data and DemandIQ will start predicting visitor volume right away.">
      
      <SuccessCheckArt className="mx-auto h-auto w-[180px] text-brand-500 md:hidden" />

      <h1 className="mt-4 text-center text-h2 font-bold leading-tight tracking-tight text-slate-900 md:mt-0 md:text-left">
        You&apos;re all set!
      </h1>
      <p className="mt-1.5 text-center text-body-xs text-slate-600 md:text-left">
        Your account has been verified.
      </p>
      <p
        className="mt-0.5 text-center text-body-xs font-medium text-brand-600 md:text-left"
        aria-live="polite">
        
        Redirecting you to the dashboard…
      </p>

      <button
        type="button"
        onClick={onEnterApp}
        className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
        
        <HomeIcon className="h-4 w-4" strokeWidth={2} />
        Go to Dashboard
      </button>

      <p className="mt-3 flex items-center justify-center gap-2 text-caption text-slate-500 lg:justify-start">
        <LoaderIcon className="h-3.5 w-3.5 animate-spin text-brand-500" strokeWidth={2} />
        Taking you to DemandIQ…
      </p>
    </AuthLayout>);

}