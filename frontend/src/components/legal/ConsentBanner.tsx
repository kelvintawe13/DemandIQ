import { useEffect, useState } from 'react';
import { CookieIcon, XIcon } from 'lucide-react';
import type { CookieChoice } from '../../contexts/LegalContext';

interface ConsentBannerProps {
  open: boolean;
  current?: CookieChoice;
  onDecide: (choice: CookieChoice) => void;
  onReadPolicy: () => void;
  onDismiss: () => void;
}

/**
 * Shown once per device until a choice is stored. Sits above the mobile
 * bottom nav so it never covers navigation.
 */
export function ConsentBanner({
  open,
  current,
  onDecide,
  onReadPolicy,
  onDismiss
}: ConsentBannerProps) {
  const [customising, setCustomising] = useState(false);
  const [preferences, setPreferences] = useState(current?.preferences ?? true);
  const [analytics, setAnalytics] = useState(current?.analytics ?? false);

  useEffect(() => {
    if (!open) setCustomising(false);
  }, [open]);

  if (!open) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[65] flex justify-center px-3 pb-[calc(env(safe-area-inset-bottom)+76px)] sm:px-4 sm:pb-4 md:justify-end md:pb-5 md:pr-5">
      <div
        role="dialog"
        aria-label="Cookie preferences"
        className="pointer-events-auto w-full max-w-[560px] rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_18px_44px_rgba(15,23,42,0.18)] sm:p-5 dark:border-slate-700 dark:bg-slate-900">
        
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-50 dark:bg-slate-800">
            <CookieIcon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.9} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-body-sm font-semibold text-slate-900 dark:text-slate-100">
              Cookies on DemandIQ
            </h2>
            <p className="mt-1 text-caption leading-relaxed text-slate-600 dark:text-slate-400">
              We use a small number of cookies to keep you signed in and to remember
              your theme, language and filters. Product analytics are optional and off
              until you allow them. No advertising or cross-site tracking, ever.
            </p>
          </div>
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss for now"
            className="tap-expand -mr-1 -mt-1 shrink-0 rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:hover:bg-slate-800">
            
            <XIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>

        {customising &&
        <div className="mt-3 flex flex-col gap-1.5">
            <div className="flex items-start gap-2.5 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800">
              <input
              type="checkbox"
              checked
              disabled
              aria-label="Strictly necessary cookies, always on"
              className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-brand-600" />
            
              <span className="min-w-0">
                <span className="block text-caption font-semibold text-slate-800 dark:text-slate-200">
                  Strictly necessary · always on
                </span>
                <span className="block text-micro leading-relaxed text-slate-500 dark:text-slate-400">
                  Sign-in session and bot protection.
                </span>
              </span>
            </div>

            <label className="flex items-start gap-2.5 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <input
              type="checkbox"
              checked={preferences}
              onChange={(event) => setPreferences(event.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-brand-600" />
            
              <span className="min-w-0">
                <span className="block text-caption font-semibold text-slate-800 dark:text-slate-200">
                  Preferences
                </span>
                <span className="block text-micro leading-relaxed text-slate-500 dark:text-slate-400">
                  Theme, language, sidebar and saved filters.
                </span>
              </span>
            </label>

            <label className="flex items-start gap-2.5 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <input
              type="checkbox"
              checked={analytics}
              onChange={(event) => setAnalytics(event.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-brand-600" />
            
              <span className="min-w-0">
                <span className="block text-caption font-semibold text-slate-800 dark:text-slate-200">
                  Product analytics
                </span>
                <span className="block text-micro leading-relaxed text-slate-500 dark:text-slate-400">
                  Aggregated, first-party usage only.
                </span>
              </span>
            </label>
          </div>
        }

        <div className="mt-3.5 flex flex-col gap-2 sm:flex-row sm:items-center">
          <button
            type="button"
            onClick={() =>
            customising ?
            onDecide({ preferences, analytics }) :
            onDecide({ preferences: true, analytics: true })
            }
            className="inline-flex items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-2">
            
            {customising ? 'Save choices' : 'Accept all'}
          </button>
          <button
            type="button"
            onClick={() => onDecide({ preferences: true, analytics: false })}
            className="inline-flex items-center justify-center rounded-lg border border-slate-300 px-4 py-2.5 text-body-xs font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-2 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">
            
            Essentials only
          </button>
          {!customising &&
          <button
            type="button"
            onClick={() => setCustomising(true)}
            className="rounded text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-slate-300">
            
              Customise
            </button>
          }
          <button
            type="button"
            onClick={onReadPolicy}
            className="rounded text-meta font-semibold text-slate-500 underline-offset-2 transition-colors duration-150 ease-out hover:text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:ml-auto dark:text-slate-400">
            
            Cookie Policy
          </button>
        </div>
      </div>
    </div>);

}