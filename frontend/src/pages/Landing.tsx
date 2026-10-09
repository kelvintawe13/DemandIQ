import { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useLegal } from '../contexts/LegalContext';
import { Reveal, RevealGroup, RevealItem } from '../components/motion/Reveal';
import {
  ArrowRightIcon,
  PlayCircleIcon,
  TrendingUpIcon,
  ClipboardCheckIcon,
  BellRingIcon,
  MenuIcon,
  XIcon,
  PlugZapIcon,
  CalendarCheckIcon,
  LeafIcon } from
'lucide-react';
import { BrandLogo } from '../components/auth/BrandLogo';
import {
  landingFeatures,
  heroForecast,
  evidencePoints,
  landingSteps } from
'../data/landing';

interface LandingProps {
  onSignIn: () => void;
  onGetStarted: () => void;
}

const featureIcon = [TrendingUpIcon, ClipboardCheckIcon, BellRingIcon];

const navLinks = [
{ id: 'features', label: 'Features' },
{ id: 'how-it-works', label: 'How it Works' },
{ id: 'community', label: 'Community' },
{ id: 'pricing', label: 'Pricing' }];


/* Shared control classes, so one screen cannot drift from itself. Durations and
   easings are the DESIGN.md § Motion values; press feedback is 0.96, never less. */
const EASE = [0.23, 1, 0.32, 1] as const;

const navLink =
'rounded-md px-1 py-2 text-body-sm font-medium text-slate-600 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';

const navGhost =
'rounded-lg px-3 py-2 text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 whitespace-nowrap';

const navPrimary =
'btn-rail inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-4 py-2 text-body-sm font-semibold text-white transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 whitespace-nowrap';

const btnPrimary =
'btn-rail inline-flex items-center justify-center gap-2 rounded-xl bg-brand-700 px-5 py-3 text-body-sm font-semibold text-white shadow-lift transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2';

const btnGhost =
'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-5 py-3 text-body-sm font-semibold text-slate-700 transition-[background-color,border-color,transform] duration-150 ease-out hover:border-slate-400 hover:bg-slate-50 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';

const learnMore =
'inline-flex w-fit items-center gap-1.5 text-body-sm font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';

const footerLink =
'rounded-md py-1 text-caption text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';

const container = 'mx-auto w-full max-w-[1360px] px-4 sm:px-6 lg:px-8';


export function Landing({ onSignIn, onGetStarted }: LandingProps) {
  const { openLegal } = useLegal();
  const [menuOpen, setMenuOpen] = useState(false);
  const reduceMotion = useReducedMotion();

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ?
      'auto' :
      'smooth',
      block: 'start'
    });
  };

  const [leadFeature, ...supportFeatures] = landingFeatures;

  return (
    /* This box is the landing page's own scroll container, and deliberately so.
       `#root` carries `overflow: hidden` so the authenticated app shell cannot
       scroll as a whole, which makes `#root` a scrollport — and `position:
       sticky` binds to the nearest scrollport, not the viewport. With the page
       scrolling on the document, the header was stuck to a scrollport that never
       moved, so it scrolled away with the content. Scrolling inside this element
       gives the sticky header something real to stick to, and keeps the fix off
       the app shell entirely. */
    <div className="h-[100dvh] w-full overflow-y-auto overscroll-contain bg-white text-slate-900">
      {/* Nav */}
      <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/90 backdrop-blur">
        <div className={`${container} flex h-16 items-center justify-between gap-4`}>
          <BrandLogo size="sm" />

          <nav aria-label="Primary" className="hidden items-center gap-8 md:flex">
            {navLinks.map((link) =>
            <button key={link.id} type="button" onClick={() => scrollTo(link.id)} className={navLink}>
                {link.label}
              </button>
            )}
          </nav>

          <div className="flex items-center gap-2">
            {/* Below `sm` the logo, Sign In, Sign Up and the menu button do not
                fit on one 320px row — the controls overhung the viewport by
                ~57px. Sign In moves into the menu panel instead of wrapping or
                shrinking, so both actions stay reachable and the row stays
                inside the screen. */}
            <button type="button" onClick={onSignIn} className={`${navGhost} hidden sm:inline-flex`}>
              Sign In
            </button>
            <button type="button" onClick={onGetStarted} className={navPrimary}>
              <span className="hidden sm:inline">Get Started Free</span>
              <span className="sm:hidden">Sign Up</span>
              <ArrowRightIcon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label="Menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
              className="rounded-lg p-2.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-100 md:hidden">
              
              {menuOpen ?
              <XIcon className="h-5 w-5" strokeWidth={2} aria-hidden="true" /> :
              <MenuIcon className="h-5 w-5" strokeWidth={2} aria-hidden="true" />}
              
            </button>
          </div>
        </div>

        {menuOpen &&
        <div className="border-t border-slate-200 bg-white px-4 py-3 md:hidden">
            <ul className="flex flex-col gap-1">
              {navLinks.map((link) =>
            <li key={link.id}>
                  <button
                type="button"
                onClick={() => scrollTo(link.id)}
                className="block w-full rounded-lg px-2 py-2.5 text-left text-body-sm font-medium text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50">
                
                    {link.label}
                  </button>
                </li>
            )}
            </ul>
            <button
            type="button"
            onClick={onGetStarted}
            className={`${navPrimary} mt-2 w-full justify-center py-2.5`}>
            
              Get Started Free
            </button>
            <button
            type="button"
            onClick={() => {
              setMenuOpen(false);
              onSignIn();
            }}
            className={`${navGhost} mt-1 w-full justify-center py-2.5 sm:hidden`}>
            
              Sign In
            </button>
          </div>
        }
      </header>

      <main>
        {/* Hero */}
        <section className="relative overflow-hidden">
          <div aria-hidden="true" className="wash-brand pointer-events-none absolute inset-0" />
          <div aria-hidden="true" className="grain-overlay pointer-events-none absolute inset-0" />
          

          <div className={`${container} relative grid grid-cols-1 items-center gap-12 py-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-20 lg:py-24`}>
            <motion.div
              initial={reduceMotion ? false : { opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: EASE }}>
              
              <p className="text-overline font-semibold uppercase text-brand-700">
                Demand forecasting for food service
              </p>

              <h1 className="mt-4 text-h1 text-slate-900">
                <motion.span
                  className="block"
                  initial={reduceMotion ? false : { opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.45, ease: EASE }}>
                  
                  Predict Demand.
                </motion.span>
                <motion.span
                  className="mt-1 block"
                  initial={reduceMotion ? false : { opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.45, ease: EASE, delay: 0.08 }}>
                  
                  Prevent Waste.
                </motion.span>
                <motion.span
                  className="mt-1 block"
                  initial={reduceMotion ? false : { opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.45, ease: EASE, delay: 0.16 }}>
                  
                  Grow <span className="text-brand-600">Smarter.</span>
                </motion.span>
              </h1>

              <p className="mt-6 measure text-lead text-slate-600">
                Restaurants and food vendors decide how much to prepare before they know
                how many people will walk in. DemandIQ turns the records you already keep
                into a daily forecast, a likely range, and a prep quantity you can
                defend.
              </p>

              <div className="mt-8 flex flex-wrap items-center gap-3">
                <button type="button" onClick={onGetStarted} className={btnPrimary}>
                  Start Free Trial
                  <ArrowRightIcon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
                </button>
                <button type="button" onClick={() => scrollTo('how-it-works')} className={btnGhost}>
                  <PlayCircleIcon className="h-4 w-4 text-brand-600" strokeWidth={2} aria-hidden="true" />
                  See how it works
                </button>
              </div>

              <dl className="mt-10 grid max-w-[540px] grid-cols-3 gap-6 border-t border-slate-200 pt-6">
                {[
                ['2 weeks', 'history to get started'],
                ['A range', 'not a single number'],
                ['4 ways', 'to bring your data in']].
                map(([value, label]) =>
                <div key={label}>
                    <dt className="text-stat font-semibold tabular-nums text-slate-900">
                      {value}
                    </dt>
                    <dd className="mt-1.5 text-caption leading-snug text-slate-500">
                      {label}
                    </dd>
                  </div>
                )}
              </dl>
            </motion.div>

            {/* Right column: what the product actually gives you each morning. */}
            <motion.div
              initial={reduceMotion ? false : { opacity: 0, y: 22 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.42, ease: EASE, delay: 0.08 }}
              className="relative">
              
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-lift-lg ring-1 ring-slate-900/5 sm:p-6">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-body-sm font-semibold text-slate-900">
                      {heroForecast.location}
                    </p>
                    <p className="text-meta text-slate-500">{heroForecast.date}</p>
                  </div>
                  <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-caption font-semibold text-brand-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-brand-500" aria-hidden="true" />
                    {heroForecast.coverage}% interval
                  </span>
                </div>

                <div className="mt-5 flex items-end justify-between gap-4">
                  <div>
                    <p className="text-overline font-semibold uppercase text-slate-500">
                      Expected customers
                    </p>
                    <p className="mt-1 text-stat-lg font-semibold tabular-nums text-slate-900">
                      {heroForecast.point}
                    </p>
                    <p className="mt-1 text-meta text-slate-500">
                      likely {heroForecast.lower}–{heroForecast.upper}
                    </p>
                  </div>
                  <div className="flex h-[58px] items-end gap-1.5" aria-hidden="true">
                    {heroForecast.trend.map((height, index) =>
                    <motion.span
                      key={index}
                      className={[
                      'w-2.5 rounded-t-sm',
                      index === heroForecast.trend.length - 1 ? 'bg-brand-600' : 'bg-brand-200'].
                      join(' ')}
                      initial={reduceMotion ? false : { height: 0 }}
                      animate={{ height: `${height}%` }}
                      transition={{ duration: 0.4, ease: EASE, delay: 0.2 + index * 0.04 }} />
                    )}
                  </div>
                </div>

                {/* Interval bar with the prep point marked on it. */}
                <div className="mt-5">
                  <div className="relative h-2 w-full rounded-full bg-slate-100">
                    <motion.div
                      className="absolute inset-y-0 left-[14%] right-[12%] rounded-full bg-brand-200"
                      initial={reduceMotion ? false : { scaleX: 0 }}
                      animate={{ scaleX: 1 }}
                      transition={{ duration: 0.45, ease: EASE, delay: 0.3 }} />
                    
                    <span
                      className="absolute left-[52%] top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-brand-700" />
                    
                    <span
                      className="absolute left-[72%] top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-amber-500" />
                    
                  </div>
                  {/* Wraps rather than squeezing: at 320px the two legend entries
                      are wider than the card. */}
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-caption text-slate-500">
                    <span className="flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-brand-700" aria-hidden="true" />
                      Forecast
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />
                      Prepare {heroForecast.prep} (safer on busy days)
                    </span>
                  </div>
                </div>

                <ul className="mt-5 flex flex-col gap-1.5 border-t border-slate-100 pt-4">
                  {heroForecast.drivers.map((driver) =>
                  <li
                    key={driver.label}
                    className="flex items-center justify-between gap-3 text-meta">
                    
                      <span className="min-w-0 truncate text-slate-600">
                        {driver.label}
                      </span>
                      <span
                      className={[
                      'shrink-0 rounded px-1.5 py-0.5 font-semibold tabular-nums',
                      driver.impact.startsWith('−') ?
                      'bg-slate-100 text-slate-600' :
                      'bg-brand-50 text-brand-800'].
                      join(' ')}>
                      
                        {driver.impact}
                      </span>
                    </li>
                  )}
                </ul>
              </div>

              {/* Sources feeding the card above. */}
              <motion.ul
                initial={reduceMotion ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: EASE, delay: 0.35 }}
                className="mt-3 flex flex-wrap items-center gap-2">
                
                {['MoMo', 'POS export', 'Spreadsheet', 'Handwritten photo'].map((src) =>
                <li
                  key={src}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-caption font-medium text-slate-600">
                  
                    <PlugZapIcon className="h-3.5 w-3.5 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                    {src}
                  </li>
                )}
              </motion.ul>
            </motion.div>
          </div>
        </section>

        {/* Features — one decisive asymmetry instead of three equal cards. */}
        <section id="features" className="scroll-mt-20 bg-slate-50 py-16 lg:py-24">
          <div className={container}>
            <p className="text-overline font-semibold uppercase text-brand-700">
              Features
            </p>
            <h2 className="mt-3 max-w-[760px] title-balance text-h2-lg text-slate-900">
              Forecasts you can question, decisions you can act on.
            </h2>

            <RevealGroup className="mt-10 grid grid-cols-1 gap-4 lg:grid-cols-12">
              {leadFeature &&
              <RevealItem className="flex h-full flex-col rounded-2xl border border-slate-200 bg-white p-6 shadow-lift transition-colors duration-150 ease-out hover:border-brand-300 lg:col-span-7 sm:p-8">
                  <span className="text-overline font-semibold uppercase text-brand-700">
                    01
                  </span>
                  <span className="mt-6 flex h-12 w-12 items-center justify-center rounded-xl bg-brand-50">
                    <TrendingUpIcon className="h-6 w-6 text-brand-600" strokeWidth={1.75} aria-hidden="true" />
                  </span>
                  <h3 className="mt-5 text-h3 text-slate-900">{leadFeature.title}</h3>
                  <p className="mt-3 measure text-body-sm leading-relaxed text-slate-600">
                    {leadFeature.description}
                  </p>
                  <button
                  type="button"
                  onClick={() => scrollTo('how-it-works')}
                  className={`${learnMore} mt-auto pt-6`}>
                  
                    Learn more
                    <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  </button>
                </RevealItem>
              }

              <div className="flex flex-col gap-4 lg:col-span-5">
                {supportFeatures.map((feature, index) => {
                  const Icon = featureIcon[index + 1];
                  return (
                    <RevealItem
                      key={feature.id}
                      className="flex flex-1 items-start gap-4 rounded-2xl border border-slate-200 bg-white p-5 transition-colors duration-150 ease-out hover:border-brand-300 sm:p-6">
                      
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-50">
                        <Icon className="h-5 w-5 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                      </span>
                      <div className="flex min-w-0 flex-col">
                        <span className="text-overline font-semibold uppercase text-slate-500">
                          0{index + 2}
                        </span>
                        <h3 className="mt-1 text-h3 text-slate-900">{feature.title}</h3>
                        <p className="mt-2 text-body-sm leading-relaxed text-slate-600">
                          {feature.description}
                        </p>
                        <button
                          type="button"
                          onClick={() => scrollTo('how-it-works')}
                          className={`${learnMore} mt-4`}>
                          
                          Learn more
                          <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                        </button>
                      </div>
                    </RevealItem>);

                })}
              </div>
            </RevealGroup>
          </div>
        </section>

        {/* Community — merged with "who uses it" so no section is a flat strip. */}
        <section id="community" className="scroll-mt-20 py-16 lg:py-24">
          <div className={container}>
            <Reveal>
              <p className="text-overline font-semibold uppercase text-brand-700">
                Who uses DemandIQ
              </p>
              <h2 className="mt-3 max-w-[760px] title-balance text-h2 text-slate-900">
                Kitchens, canteens and fresh-food vendors across Kigali
              </h2>
              <p className="mt-4 measure text-body-sm leading-relaxed text-slate-600">
                DemandIQ is designed for restaurants, cafeterias, and fresh-food vendors who want to
                reduce waste through data-driven preparation decisions.
              </p>
            </Reveal>

            <RevealGroup className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* Two things the product actually does, rather than a performance
                  number. The old tile here read "90% of days land in our range",
                  which is the model's *target* interval coverage — nothing in the
                  run manifests measures realised coverage, so the page was
                  claiming a result it could not evidence. */}
              <RevealItem className="flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-lift">
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand-50">
                  <CalendarCheckIcon className="h-6 w-6 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                </span>
                <span className="min-w-0">
                  <span className="block text-h3 font-semibold text-slate-900">
                    Per location
                  </span>
                  <span className="block text-caption text-slate-500">
                    each site forecast on its own, not one blended total
                  </span>
                </span>
              </RevealItem>
              <RevealItem className="flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-lift">
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand-50">
                  <LeafIcon className="h-6 w-6 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                </span>
                <span className="min-w-0">
                  <span className="block text-h3 font-semibold text-slate-900">
                    Always learning
                  </span>
                  <span className="block text-caption text-slate-500">
                    updates as new data arrives
                  </span>
                </span>
              </RevealItem>
            </RevealGroup>
          </div>
        </section>

        {/* Evidence */}
        <section className="relative overflow-hidden border-y border-slate-200 bg-brand-900 py-16 lg:py-20">
          <div aria-hidden="true" className="grain-overlay pointer-events-none absolute inset-0" />
          <div className={`${container} relative`}>
            <Reveal>
              <h2 className="text-overline font-semibold uppercase text-brand-200">
                Why this matters
              </h2>
            </Reveal>
            <RevealGroup as="ul" className="mt-8 grid grid-cols-2 gap-x-8 gap-y-10 lg:grid-cols-4">
              {evidencePoints.map((point) =>
              <RevealItem as="li" key={point.id} className="flex flex-col">
                  <span className="text-stat font-semibold tabular-nums text-white sm:text-stat-lg">
                    {point.value}
                  </span>
                  <span className="mt-2 text-body-sm leading-relaxed text-brand-100">
                    {point.label}
                  </span>
                  <span className="mt-auto pt-3 text-caption text-brand-300">
                    {point.source}
                  </span>
                </RevealItem>
              )}
            </RevealGroup>
          </div>
        </section>

        {/* How it works — a numbered rail rather than three icon blobs. */}
        <section id="how-it-works" className="scroll-mt-20 bg-slate-50 py-16 lg:py-24">
          <div className={container}>
            <Reveal>
              <p className="text-overline font-semibold uppercase text-brand-700">
                How it works
              </p>
              <h2 className="mt-3 max-w-[720px] title-balance text-h2-lg text-slate-900">
                From your records to tomorrow's prep number
              </h2>
            </Reveal>

            <RevealGroup className="mt-12">
              <ol className="grid grid-cols-1 gap-x-10 gap-y-10 md:grid-cols-3">
                {landingSteps.map((step, index) =>
                <RevealItem as="li" key={step.id} className="relative">
                    <div className="flex items-center gap-3">
                      <span className="text-h2 font-semibold tabular-nums text-brand-700">
                        0{index + 1}
                      </span>
                      <span aria-hidden="true" className="h-px flex-1 bg-slate-200" />
                    </div>
                    <h3 className="mt-4 text-h3 text-slate-900">{step.title}</h3>
                    <p className="mt-2 text-body-sm leading-relaxed text-slate-600">
                      {step.description}
                    </p>
                  </RevealItem>
                )}
              </ol>
            </RevealGroup>
          </div>
        </section>

        {/* CTA */}
        <section id="pricing" className="scroll-mt-20 py-16 lg:py-24">
          <div className={container}>
            <Reveal className="relative overflow-hidden rounded-3xl bg-brand-800 px-6 py-14 text-center shadow-lift-lg lg:px-16 lg:py-20">
              <div aria-hidden="true" className="grain-overlay pointer-events-none absolute inset-0" />
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.12),transparent_60%)]" />

              <h2 className="relative mx-auto max-w-[720px] title-balance text-h2-lg text-white">
                Ready to stop guessing and start forecasting?
              </h2>
              <button
                type="button"
                onClick={onGetStarted}
                className="relative mt-8 inline-flex items-center justify-center gap-2 rounded-xl bg-white px-6 py-3 text-body-sm font-semibold text-brand-800 transition-[background-color,transform] duration-150 ease-out hover:bg-brand-50 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-brand-800">

                Create Free Account
                <ArrowRightIcon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
              </button>
              <p className="relative mt-4 text-caption text-brand-100">
                No credit card required · Free 14-day trial · Cancel anytime.
              </p>
            </Reveal>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white">
        <Reveal className={`${container} grid grid-cols-2 gap-8 py-12 md:grid-cols-4`}>
          <div className="col-span-2 md:col-span-1">
            <BrandLogo size="sm" />
            <p className="mt-3 max-w-[240px] text-caption leading-relaxed text-slate-500">
              The explainable AI platform for demand forecasting and food waste
              prevention.
            </p>
          </div>

          {[
          {
            title: 'Product',
            links: [
            { label: 'Features', action: () => scrollTo('features') },
            { label: 'How it Works', action: () => scrollTo('how-it-works') },
            { label: 'Pricing', action: () => scrollTo('pricing') },
            { label: 'Create account', action: onGetStarted }]

          },
          {
            title: 'Company',
            links: [
            { label: 'Community', action: () => scrollTo('community') },
            { label: 'Sign in', action: onSignIn },
            { label: 'Careers', action: () => scrollTo('community') },
            { label: 'Contact', action: () => scrollTo('pricing') }]

          },
          {
            title: 'Resources',
            links: [
            { label: 'Help Center', action: () => scrollTo('features') },
            { label: 'Guides', action: () => scrollTo('how-it-works') },
            { label: 'Templates', action: () => scrollTo('features') },
            { label: 'Compliance', action: () => scrollTo('pricing') }]

          }].
          map((column) =>
          <div key={column.title}>
              <p className="text-caption font-semibold text-slate-900">{column.title}</p>
              <ul className="mt-3 flex flex-col gap-1">
                {column.links.map((link) =>
              <li key={link.label}>
                    <button
                  type="button"
                  onClick={link.action}
                  className={footerLink}>
                  
                      {link.label}
                    </button>
                  </li>
              )}
              </ul>
            </div>
          )}
        </Reveal>

        <div className="border-t border-slate-200">
          <div className={`${container} flex flex-wrap items-center justify-between gap-2 py-5 text-caption text-slate-500`}>
            <span>© 2025 DemandIQ. All rights reserved.</span>
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
              {[
              { label: 'Privacy Policy', id: 'privacy' as const },
              { label: 'Terms of Service', id: 'terms' as const },
              { label: 'Cookie Policy', id: 'cookies' as const }].
              map((item) =>
              <button
                key={item.id}
                type="button"
                onClick={() => openLegal(item.id)}
                className={footerLink}>
                
                  {item.label}
                </button>
              )}
            </span>
          </div>
        </div>
      </footer>
    </div>);

}
