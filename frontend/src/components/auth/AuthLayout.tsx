import React from 'react';
import { CheckIcon } from 'lucide-react';
import { BrandLogo } from './BrandLogo';
import { AuthControls } from './AuthControls';

interface AuthLayoutProps {
  children: React.ReactNode;
  art: React.ReactNode;
  artTitle: string;
  artText: string;
  highlights?: string[];
}

const defaultHighlights = [
'Forecast daily demand at dish level',
'Ingest POS, mobile money, files or handwriting',
'Cut food waste by up to 40%'];


export function AuthLayout({
  children,
  art,
  artTitle,
  artText,
  highlights = defaultHighlights
}: AuthLayoutProps) {
  return (
    <div className="app-viewport flex w-full overflow-hidden bg-brand-50 md:grid md:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
      {/* Form column */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-y-auto px-5 py-5 sm:px-8 md:py-7 lg:px-12">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(ellipse_at_top_left,_rgba(111,214,198,0.22),_transparent_60%)]" />
        <div className="relative flex items-center justify-between gap-3">
          <BrandLogo size="sm" />
          <AuthControls />
        </div>

        <div className="relative flex flex-1 items-center justify-center py-8">
          <div className="w-full max-w-[340px] sm:max-w-[368px]">{children}</div>
        </div>

        <p className="relative text-center text-fine text-slate-500 md:text-left">
          © 2026 DemandIQ · Demo environment, no real credentials required.
        </p>
      </div>

      {/* Brand column — full-bleed teal plane, art as atmosphere not a card */}
      <aside
        className="relative hidden flex-col justify-end overflow-hidden bg-brand-900 px-8 pb-10 pt-12 md:flex lg:px-12 lg:pb-12"
        aria-hidden="true">
        
        <div
          className="pointer-events-none absolute inset-0 opacity-30"
          style={{
            backgroundImage: 'radial-gradient(circle, #6fd6c6 1px, transparent 1px)',
            backgroundSize: '22px 22px'
          }} />
        
        <div
          className="pointer-events-none absolute -right-28 -top-20 h-[420px] w-[420px] rounded-full bg-brand-700/50 blur-2xl" />
        <div
          className="pointer-events-none absolute -bottom-32 -left-24 h-[380px] w-[380px] rounded-full bg-brand-800/80 blur-xl" />

        <div className="relative mx-auto flex w-full max-w-[440px] flex-col">
          <p className="font-display text-h4 tracking-tight text-brand-300">
            Demand<span className="text-brand-200">IQ</span>
          </p>
          <div className="mt-6 flex justify-center text-brand-200">{art}</div>

          <h2 className="animate-title-in mt-8 font-display text-display leading-[1.12] tracking-tight text-white">
            {artTitle}
          </h2>
          <p className="animate-subtitle-in mt-3 text-control leading-relaxed text-brand-100/75">{artText}</p>

          <ul className="mt-6 flex flex-col gap-2.5">
            {highlights.map((item) =>
            <li key={item} className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand-400 text-brand-900">
                  <CheckIcon className="h-2.5 w-2.5" strokeWidth={3.5} />
                </span>
                <span className="text-body-xs text-brand-50/90">{item}</span>
              </li>
            )}
          </ul>
        </div>
      </aside>
    </div>);

}