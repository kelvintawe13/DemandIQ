/**
 * Cloudflare Turnstile loader + configuration probe.
 *
 * Split out of `components/auth/TurnstileWidget.tsx` so that module exports only
 * its component. Pages need the `isTurnstileEnabled` probe to gate submission;
 * exporting a plain function beside a component defeats Fast Refresh
 * (eslint-plugin-react-refresh's `only-export-components`).
 *
 * Without `VITE_TURNSTILE_SITE_KEY` the widget renders nothing at all (a human
 * check that checks nothing is worse than none), and
 * `backend/app/security/human.py` skips verification when its TURNSTILE_SECRET
 * is unset — so local dev stays frictionless while production enforces a real
 * challenge on both ends.
 */

export type TurnstileApi = {
  render: (
    el: HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      'error-callback'?: (code: string) => void;
      'expired-callback'?: () => void;
      theme?: 'light' | 'dark' | 'auto';
    },
  ) => string;
  reset: (widgetId?: string) => void;
  remove: (widgetId: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    onTurnstileLoaded?: () => void;
  }
}

const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

/** Empty unless `VITE_TURNSTILE_SITE_KEY` is configured. */
export const TURNSTILE_SITE_KEY =
  (import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined) ?? '';

let scriptPromise: Promise<TurnstileApi> | null = null;

/** True when the real widget is configured (pages gate submission on it). */
export function isTurnstileEnabled(): boolean {
  return TURNSTILE_SITE_KEY !== '';
}

export function loadTurnstileScript(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      window.onTurnstileLoaded = () => {
        if (window.turnstile) resolve(window.turnstile);
        else reject(new Error('Turnstile loaded but API missing'));
      };
      const script = document.createElement('script');
      script.src = SCRIPT_SRC;
      script.async = true;
      script.defer = true;
      script.onload = () => window.onTurnstileLoaded?.();
      script.onerror = () => {
        scriptPromise = null;
        reject(new Error('Failed to load Turnstile script'));
      };
      document.head.appendChild(script);
    });
  }
  return scriptPromise;
}
