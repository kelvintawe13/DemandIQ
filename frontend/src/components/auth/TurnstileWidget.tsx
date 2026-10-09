import { useCallback, useEffect, useRef, useState } from 'react';
import { TURNSTILE_SITE_KEY, loadTurnstileScript } from '../../lib/turnstile';

interface TurnstileWidgetProps {
  /** Fired once the challenge resolves with a real Turnstile token. */
  onVerify: (token: string) => void;
  /** Fired when the token expires or the widget errors; parent should clear its token. */
  onExpire?: () => void;
  error?: string;
}

/**
 * Real Cloudflare Turnstile (managed widget, explicit render).
 *
 * Renders only when VITE_TURNSTILE_SITE_KEY is configured; without a site
 * key the placeholder panel is shown; backend/app/security/human.py skips
 * verification when its TURNSTILE_SECRET is unset, so local dev stays
 * frictionless while production enforces a real challenge on both ends.
 *
 * IPv6 caveat: brunhild.challenges.cloudflare.com (challenge host) is
 * IPv6-only; on IPv4-only networks the widget never resolves. Leave BOTH
 * keys unset in that environment to skip the check (see frontend/.env.example
 * and backend/.env.example). The siteverify host (challenges.cloudflare.com)
 * has IPv4 and works for server-side verification when reachable.
 */
export function TurnstileWidget({ onVerify, onExpire, error }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const verifyRef = useRef(onVerify);
  verifyRef.current = onVerify;
  const [failed, setFailed] = useState(false);
  const [solved, setSolved] = useState(false);

  const handleToken = useCallback((token: string) => {
    setSolved(true);
    setFailed(false);
    verifyRef.current(token);
  }, []);

  const expireRef = useRef(onExpire);
  expireRef.current = onExpire;

  useEffect(() => {
    if (!TURNSTILE_SITE_KEY) return;
    let cancelled = false;

    loadTurnstileScript()
      .then((turnstile) => {
        if (cancelled || !containerRef.current || widgetIdRef.current !== null) return;
        widgetIdRef.current = turnstile.render(containerRef.current, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: handleToken,
          'error-callback': (code: string) => {
            setFailed(true);
            setSolved(false);
            expireRef.current?.();
            // Cloudflare test keys should not hit this; real failures (e.g. IPv6-only
            // brunhild host on an IPv4-only network) surface here as well.
            console.warn('[Turnstile] error-callback', code);
            return undefined;
          },
          'expired-callback': () => {
            setSolved(false);
            expireRef.current?.();
            turnstile.reset(widgetIdRef.current ?? undefined);
          },
          theme: 'light',
        });
      })
      .catch(() => setFailed(true));

    return () => {
      cancelled = true;
      if (widgetIdRef.current !== null && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {}
        widgetIdRef.current = null;
      }
    };
  }, [handleToken]);

  // No site key configured — render nothing at all (product decision: never
  // show a human-check panel that is not actually checking anything). The
  // backend skips verification when TURNSTILE_SECRET is unset, so the form
  // simply submits without a challenge in this environment.
  if (!TURNSTILE_SITE_KEY) {
    return null;
  }

  return (
    <div>
      <div
        className={[
          'rounded-lg',
          error || failed ? 'border border-red-400' : '',
          solved && !error ? 'border border-brand-300' : '',
        ].join(' ')}>
        <div ref={containerRef} />
      </div>
      {(error || failed) && !solved && (
        <div className="mt-1">
          <p className="text-micro text-red-600">
            {failed
              ? 'Human check failed to load. Check your network and retry.'
              : error}
          </p>
          {failed && (
            <button
              type="button"
              onClick={() => {
                setFailed(false);
                if (widgetIdRef.current !== null && window.turnstile) {
                  try {
                    window.turnstile.reset(widgetIdRef.current);
                  } catch {}
                } else if (containerRef.current && window.turnstile) {
                  // Re-render if we never got a widget id
                  try {
                    window.turnstile.remove(widgetIdRef.current ?? '');
                  } catch {}
                  widgetIdRef.current = null;
                  // trigger re-render by re-running effect: force reload
                  window.location.reload();
                }
              }}
              className="mt-1 text-micro font-medium text-brand-700 hover:text-brand-800">
              Retry human check
            </button>
          )}
        </div>
      )}
    </div>
  );
}
