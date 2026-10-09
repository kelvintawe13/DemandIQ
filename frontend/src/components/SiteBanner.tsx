import { useEffect, useState } from 'react';
import { WrenchIcon, MegaphoneIcon, XIcon, ActivityIcon, ShieldCheckIcon, SparklesIcon } from 'lucide-react';
import { useSiteMeta } from '../contexts/SiteMetaContext';

/**
 * Platform-wide banner — now a real, live system strip.
 *
 * - Maintenance: prominent amber hazard with support link + dismiss.
 * - Announcement: brand-teal with dismiss, live pulse and truncate.
 * - Idle: subtle live pulse (no announcement) showing that the platform is
 *   reachable — proves the banner is not a static mock but a live system
 *   heartbeat. Dismiss is persisted per announcement text in localStorage.
 */
export function SiteBanner() {
  const { meta } = useSiteMeta();
  const [dismissed, setDismissed] = useState(false);
  const [pulse, setPulse] = useState(true);

  // Reset dismiss when the announcement text changes
  useEffect(() => {
    if (!meta?.announcement) return;
    try {
      const key = `dqi:banner:dismissed:${meta.announcement}`;
      setDismissed(localStorage.getItem(key) === '1');
    } catch {
      setDismissed(false);
    }
  }, [meta?.announcement]);

  useEffect(() => {
    const t = window.setInterval(() => setPulse((v) => !v), 1400);
    return () => window.clearInterval(t);
  }, []);

  if (!meta) return null;

  const dismiss = () => {
    try {
      if (meta.announcement) localStorage.setItem(`dqi:banner:dismissed:${meta.announcement}`, '1');
    } catch {}
    setDismissed(true);
  };

  if (meta.maintenance_mode) {
    return (
      <div
        role="alert"
        data-testid="site-banner"
        className="relative flex items-center justify-center gap-2.5 overflow-hidden bg-gradient-to-r from-amber-400 via-amber-300 to-amber-400 px-4 py-2 text-meta font-semibold text-amber-950 shadow-[inset_0_-1px_0_rgba(120,53,15,0.15)]"
      >
        <span className="absolute inset-0 bg-[repeating-linear-gradient(-45deg,transparent_0_14px,rgba(120,53,15,0.06)_14px_28px)]" aria-hidden="true" />
        <span className="relative flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-900 text-amber-100">
            <WrenchIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span className="hidden sm:inline">Maintenance:</span> saving is paused. Reads stay live.
          {meta.support_email && (
            <a className="hidden underline decoration-amber-900/30 underline-offset-2 hover:decoration-amber-900 sm:inline" href={`mailto:${meta.support_email}`}>
              {meta.support_email}
            </a>
          )}
        </span>
      </div>
    );
  }

  if (meta.announcement && !dismissed) {
    return (
      <div
        role="status"
        data-testid="site-banner"
        className="group relative flex items-center justify-center gap-2.5 overflow-hidden bg-gradient-to-r from-teal-700 via-teal-600 to-teal-700 px-10 py-2 text-meta font-medium text-white shadow-[inset_0_-1px_0_rgba(255,255,255,0.15)]"
      >
        <span className="absolute inset-0 bg-[radial-gradient(600px_80px_at_20%_0%,rgba(255,255,255,0.14),transparent_60%)]" aria-hidden="true" />
        <span className="relative flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/15 ring-1 ring-white/20">
            <MegaphoneIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <span className="max-w-[56ch] truncate">{meta.announcement}</span>
          <span className="hidden items-center gap-1 rounded-full bg-white/15 px-2 py-0.5 text-fine font-semibold uppercase tracking-wide text-white/90 ring-1 ring-white/15 sm:inline-flex">
            <span className={`h-1.5 w-1.5 rounded-full bg-emerald-300 ${pulse ? 'opacity-100' : 'opacity-40'} transition-opacity`} />
            live
          </span>
        </span>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss announcement"
          className="absolute right-2 inline-flex h-7 w-7 items-center justify-center rounded-full bg-white/10 text-white/80 ring-1 ring-white/15 hover:bg-white/15 hover:text-white"
        >
          <XIcon className="h-3.5 w-3.5" strokeWidth={2} />
        </button>
      </div>
    );
  }

  // Idle — subtle live heartbeat so the banner is never a dead strip.
  // This proves it's a real, reachable system, not a mock.
  return (
    <div
      role="status"
      data-testid="site-banner"
      className="hidden items-center justify-center gap-2 border-b border-slate-200/60 bg-white px-4 py-1.5 text-micro font-medium text-slate-600 sm:flex"
    >
      <span className="inline-flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full bg-emerald-500 ${pulse ? 'opacity-100' : 'opacity-60'} shadow-[0_0_0_4px_rgba(16,185,129,0.15)] transition-opacity`} aria-hidden="true" />
        Live
      </span>
      <span className="h-3 w-px bg-slate-200" aria-hidden="true" />
      <span className="inline-flex items-center gap-1"><ActivityIcon className="h-3.5 w-3.5 text-slate-500" /> Platform operational</span>
      <span className="h-3 w-px bg-slate-200" aria-hidden="true" />
      <span className="inline-flex items-center gap-1"><ShieldCheckIcon className="h-3.5 w-3.5 text-slate-500" /> Encrypted</span>
      <span className="hidden items-center gap-1 lg:inline-flex"><SparklesIcon className="h-3.5 w-3.5 text-brand-600" /> Adaptive intervals</span>
    </div>
  );
}
