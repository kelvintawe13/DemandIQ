import { useEffect, useState } from 'react';
import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform
} from 'framer-motion';
import { useLegal } from '../contexts/LegalContext';
import {
  ArrowRightIcon,
  TrendingUpIcon,
  ClipboardCheckIcon,
  BellRingIcon,
  PlugZapIcon,
  CalendarCheckIcon,
  LeafIcon,
  PauseIcon,
  PlayIcon
} from 'lucide-react';
import { BrandLogo } from '../components/auth/BrandLogo';
import {
  landingFeatures,
  heroForecast,
  evidencePoints,
  landingSteps
} from '../data/landing';

interface LandingProps {
  onSignIn: () => void;
  onGetStarted: () => void;
}

const EASE = [0.23, 1, 0.32, 1] as const;
const AUTO_MS = 6500;
const featureIcon = [TrendingUpIcon, ClipboardCheckIcon, BellRingIcon];

const tabs = [
  { id: 'features', label: 'Features' },
  { id: 'how', label: 'How it works' },
  { id: 'evidence', label: 'Why it matters' },
  { id: 'community', label: 'Who uses it' }
] as const;
type TabId = (typeof tabs)[number]['id'];

/**
 * Where the photos in `public/` are served from.
 *
 * The built bundle is mounted at `/app/` (vite.config.ts `base`), so the files
 * copied from `public/` sit beside index.html at `/app/images/...`. A
 * root-absolute `/images/landing/...` is therefore a 404 in production, where
 * the only thing at the root is the API. `BASE_URL` is `/` in dev and vitest.
 */
const assetBase = ((import.meta.env.BASE_URL as string | undefined) ?? '/').replace(/\/+$/, '');

const heroSlides = [
  { src: `${assetBase}/images/landing/restaurant.jpg`, label: 'Restaurant dining room' },
  { src: `${assetBase}/images/landing/service.jpg`, label: 'Freshly prepared meal' },
  { src: `${assetBase}/images/landing/terrace.jpg`, label: 'Restaurant terrace' }
];

const navGhost =
  'rounded-lg px-3 py-2 text-body-sm font-semibold text-slate-200 transition-colors duration-150 ease-out hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 whitespace-nowrap';
const navPrimary =
  'btn-rail inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-4 py-2 text-body-sm font-semibold text-white transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 whitespace-nowrap';
const btnPrimary =
  'btn-rail inline-flex items-center justify-center gap-2 rounded-xl bg-brand-700 px-5 py-3 text-body-sm font-semibold text-white shadow-lift transition-[background-color,transform] duration-150 ease-out hover:bg-brand-800 active:scale-[0.96] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2';
const footerLink =
  'rounded-md py-1 text-caption text-slate-500 transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';
const container = 'mx-auto w-full px-5 sm:px-8 lg:px-12 2xl:px-16';

/* Counts the expected-customers figure up once, on load. */
function useCountUp(target: number, skip: boolean) {
  const [value, setValue] = useState(skip ? target : 0);
  useEffect(() => {
    if (skip) {
      setValue(target);
      return;
    }
    const controls = animate(0, target, {
      duration: 1.1,
      delay: 0.35,
      ease: EASE,
      onUpdate: (v) => setValue(Math.round(v))
    });
    return () => controls.stop();
  }, [target, skip]);
  return value;
}

export function Landing({ onSignIn, onGetStarted }: LandingProps) {
  const { openLegal } = useLegal();
  const reduceMotion = useReducedMotion();
  const [tab, setTab] = useState<TabId>('features');
  const [autoplay, setAutoplay] = useState(true);
  const [hovering, setHovering] = useState(false);
  const [heroSlideIndex, setHeroSlideIndex] = useState(0);
  const [heroAutoplay, setHeroAutoplay] = useState(true);
  const running = autoplay && !hovering && !reduceMotion;

  useEffect(() => {
    if (!heroAutoplay || reduceMotion) return;
    const timer = window.setInterval(() => {
      setHeroSlideIndex((current) => (current + 1) % heroSlides.length);
    }, 7000);
    return () => window.clearInterval(timer);
  }, [heroAutoplay, reduceMotion]);

  const advance = () =>
    setTab((current) => tabs[(tabs.findIndex((t) => t.id === current) + 1) % tabs.length].id);

  const choose = (id: TabId) => {
    setAutoplay(false);
    setTab(id);
  };

  const point = useCountUp(Number(heroForecast.point), !!reduceMotion);

  /* Pointer tilt on the forecast card: the one interactive moment in the hero. */
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const rotY = useSpring(useTransform(mx, [-0.5, 0.5], [-5, 5]), { stiffness: 120, damping: 18 });
  const rotX = useSpring(useTransform(my, [-0.5, 0.5], [4, -4]), { stiffness: 120, damping: 18 });
  const glowX = useTransform(mx, [-0.5, 0.5], ['25%', '75%']);
  const glowY = useTransform(my, [-0.5, 0.5], ['25%', '75%']);
  const glow = useTransform(
    [glowX, glowY],
    ([x, y]) => `radial-gradient(420px circle at ${x} ${y}, rgba(255,255,255,0.55), transparent 60%)`
  );

  const onTilt = (e: React.PointerEvent<HTMLDivElement>) => {
    if (reduceMotion) return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  };
  const offTilt = () => {
    mx.set(0);
    my.set(0);
  };

  const rise = (delay = 0, y = 16) => ({
    initial: reduceMotion ? false : { opacity: 0, y },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.45, ease: EASE, delay }
  });

  return (
    <div className="relative flex h-[100dvh] w-full flex-col overflow-y-auto bg-slate-950 text-slate-100">
      {/* Ambient layer: two slow drifting washes behind everything. */}
      <div aria-hidden="true" className="wash-brand pointer-events-none absolute inset-0" />
      <div aria-hidden="true" className="grain-overlay pointer-events-none absolute inset-0" />
      {!reduceMotion && (
        <>
          <motion.div
            aria-hidden="true"
            className="pointer-events-none absolute -right-24 -top-24 h-[420px] w-[420px] rounded-full bg-brand-200/50 blur-3xl"
            animate={{ x: [0, -40, 0], y: [0, 30, 0] }}
            transition={{ duration: 18, repeat: Infinity, ease: 'easeInOut' }}
          />
          <motion.div
            aria-hidden="true"
            className="pointer-events-none absolute -bottom-32 left-1/4 h-[360px] w-[360px] rounded-full bg-amber-200/30 blur-3xl"
            animate={{ x: [0, 50, 0], y: [0, -24, 0] }}
            transition={{ duration: 22, repeat: Infinity, ease: 'easeInOut' }}
          />
        </>
      )}

      {/* Header */}
      <header className="sticky top-0 z-10 shrink-0 border-b border-slate-800/80 bg-slate-950/60 backdrop-blur">
        <div className={`${container} flex h-14 items-center justify-between gap-4`}>
          <BrandLogo size="sm" inverse />
          <div className="flex items-center gap-2">
            <button type="button" onClick={onSignIn} className={navGhost}>
              Sign In
            </button>
            <button type="button" onClick={onGetStarted} className={navPrimary}>
              <span className="hidden sm:inline">Create Account</span>
              <span className="sm:hidden">Sign Up</span>
              <ArrowRightIcon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
            </button>
          </div>
        </div>
      </header>

      {/* Hero: fills whatever height is left between header and dock. */}
      <main className="relative z-10 flex min-h-0 flex-1 items-center overflow-hidden">
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
          <AnimatePresence initial={false}>
            <motion.img
              key={heroSlides[heroSlideIndex].src}
              src={heroSlides[heroSlideIndex].src}
              alt=""
              aria-hidden="true"
              initial={reduceMotion ? false : { opacity: 0, scale: 1.035 }}
              animate={{ opacity: 0.52, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduceMotion ? 0 : 1.2, ease: EASE }}
              className="absolute inset-0 h-full w-full object-cover"
            />
          </AnimatePresence>
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(90deg, rgba(2, 6, 23, .94) 0%, rgba(2, 6, 23, .78) 48%, rgba(2, 6, 23, .60) 100%), linear-gradient(0deg, rgba(2, 6, 23, .48), transparent 38%, rgba(2, 6, 23, .26))'
            }}
          />
        </div>

        <div className="absolute right-4 top-4 z-20 flex items-center gap-1.5 rounded-full border border-white/15 bg-slate-950/70 p-1.5 backdrop-blur sm:right-6">
          {heroSlides.map((slide, index) => (
            <button
              key={slide.src}
              type="button"
              aria-label={`Show background: ${slide.label}`}
              aria-pressed={index === heroSlideIndex}
              onClick={() => {
                setHeroSlideIndex(index);
                setHeroAutoplay(false);
              }}
              className="grid h-7 w-7 place-items-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400">
              <span
                aria-hidden="true"
                className={[
                  'h-1.5 rounded-full transition-[width,background-color] duration-200',
                  index === heroSlideIndex ? 'w-4 bg-brand-300' : 'w-1.5 bg-white/55'
                ].join(' ')}
              />
            </button>
          ))}
          <span className="mx-0.5 h-4 w-px bg-white/20" aria-hidden="true" />
          <button
            type="button"
            aria-label={heroAutoplay ? 'Pause background slideshow' : 'Play background slideshow'}
            title={heroAutoplay ? 'Pause slideshow' : 'Play slideshow'}
            onClick={() => setHeroAutoplay((playing) => !playing)}
            className="grid h-7 w-7 place-items-center rounded-full text-slate-100 transition-colors hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400">
            {heroAutoplay ? (
              <PauseIcon className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <PlayIcon className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        </div>

        <div
          className={`${container} relative z-10 grid grid-cols-1 items-center gap-8 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-16`}>
          <div>
            <motion.p {...rise(0, 8)} className="text-overline font-semibold uppercase text-brand-300">
              Demand forecasting for food service
            </motion.p>

            {/* Type scales with viewport height so the headline never forces a scroll. */}
            <h1
              className="mt-3 font-semibold leading-[1.04] tracking-tight text-slate-50"
              style={{ fontSize: 'clamp(2rem, min(5.6vh, 4.6vw), 3.5rem)' }}>
              <motion.span className="block text-slate-50" {...rise(0.05, 18)}>
                Predict Demand.
              </motion.span>
              <motion.span className="block text-slate-50" {...rise(0.13, 18)}>
                Prevent Waste.
              </motion.span>
              <motion.span className="block text-brand-300" {...rise(0.21, 18)}>
                Grow Smarter.
              </motion.span>
            </h1>

            <motion.p
              {...rise(0.3)}
              className="mt-5 hidden max-w-[52ch] text-lead text-slate-300 sm:block [@media(max-height:720px)]:hidden">
              DemandIQ turns the records you already keep into a daily forecast, a likely range,
              and a prep quantity you can defend.
            </motion.p>

            <motion.div {...rise(0.38)} className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2">
              <button type="button" onClick={onGetStarted} className={btnPrimary}>
                Create Account
                <ArrowRightIcon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
              </button>
            </motion.div>
          </div>

          {/* Forecast card: what the product gives you each morning. */}
          <motion.div
            {...rise(0.15, 22)}
            className="relative hidden lg:block"
            style={{ perspective: 1000 }}
            onPointerMove={onTilt}
            onPointerLeave={offTilt}>
            <motion.div
              style={reduceMotion ? undefined : { rotateX: rotX, rotateY: rotY }}
              className="relative overflow-hidden rounded-2xl border border-slate-700/80 bg-slate-900/80 p-5 shadow-lift-lg ring-1 ring-slate-950/60 xl:p-6">
              {!reduceMotion && (
                <motion.div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-0 opacity-70"
                  style={{ background: glow }}
                />
              )}

              <div className="relative flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-body-sm font-semibold text-slate-100">
                    {heroForecast.location}
                  </p>
                  <p className="text-meta text-slate-300">{heroForecast.date}</p>
                </div>
                <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-brand-500/10 px-2.5 py-1 text-caption font-semibold text-brand-200">
                  <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
                    {!reduceMotion && (
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-500 opacity-60" />
                    )}
                    <span className="relative h-1.5 w-1.5 rounded-full bg-brand-500" />
                  </span>
                  {heroForecast.coverage}% interval
                </span>
              </div>

              <div className="relative mt-4 flex items-end justify-between gap-4">
                <div>
                  <p className="text-overline font-semibold uppercase text-slate-300">
                    Expected customers
                  </p>
                  <p className="mt-1 text-stat-md font-semibold tabular-nums text-slate-50">
                    {point}
                  </p>
                  <p className="mt-1 text-meta text-slate-300">
                    likely {heroForecast.lower}–{heroForecast.upper}
                  </p>
                </div>
                <div className="flex h-[54px] items-end gap-1.5" aria-hidden="true">
                  {heroForecast.trend.map((height, index) => (
                    <motion.span
                      key={index}
                      className={[
                        'w-2.5 rounded-t-sm',
                        index === heroForecast.trend.length - 1 ? 'bg-brand-600' : 'bg-brand-200'
                      ].join(' ')}
                      initial={reduceMotion ? false : { height: 0 }}
                      animate={{ height: `${height}%` }}
                      transition={{ duration: 0.4, ease: EASE, delay: 0.3 + index * 0.04 }}
                    />
                  ))}
                </div>
              </div>

              <div className="relative mt-4">
                <div className="relative h-2 w-full rounded-full bg-slate-800">
                  <motion.div
                    className="absolute inset-y-0 left-[14%] right-[12%] origin-left rounded-full bg-brand-400/40"
                    initial={reduceMotion ? false : { scaleX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ duration: 0.5, ease: EASE, delay: 0.5 }}
                  />
                  <motion.span
                    className="absolute left-[52%] top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-brand-700"
                    initial={reduceMotion ? false : { opacity: 0, scaleY: 0 }}
                    animate={{ opacity: 1, scaleY: 1 }}
                    transition={{ duration: 0.3, ease: EASE, delay: 0.85 }}
                  />
                  <motion.span
                    className="absolute left-[72%] top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-amber-500"
                    initial={reduceMotion ? false : { opacity: 0, scaleY: 0 }}
                    animate={{ opacity: 1, scaleY: 1 }}
                    transition={{ duration: 0.3, ease: EASE, delay: 1 }}
                  />
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-caption text-slate-300">
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

              <ul className="relative mt-4 flex flex-col gap-1.5 border-t border-slate-700 pt-3 [@media(max-height:760px)]:hidden">
                {heroForecast.drivers.map((driver, i) => (
                  <motion.li
                    key={driver.label}
                    initial={reduceMotion ? false : { opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.3, ease: EASE, delay: 0.9 + i * 0.07 }}
                    className="flex items-center justify-between gap-3 text-meta">
                    <span className="min-w-0 truncate text-slate-300">{driver.label}</span>
                    <span
                      className={[
                        'shrink-0 rounded px-1.5 py-0.5 font-semibold tabular-nums',
                        driver.impact.startsWith('−')
                          ? 'bg-slate-800 text-slate-200'
                          : 'bg-brand-500/10 text-brand-200'
                      ].join(' ')}>
                      {driver.impact}
                    </span>
                  </motion.li>
                ))}
              </ul>
            </motion.div>

            {/* Data sources feeding the card. */}
            <motion.ul
              {...rise(0.6, 10)}
              className="mt-3 flex flex-wrap items-center gap-2">
              {['MoMo', 'POS export', 'Spreadsheet', 'Handwritten photo'].map((src) => (
                <li
                  key={src}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/90 px-2.5 py-1.5 text-caption font-medium text-slate-200">
                  <PlugZapIcon className="h-3.5 w-3.5 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                  {src}
                </li>
              ))}
            </motion.ul>
          </motion.div>
        </div>
      </main>

      {/* Dock: replaces the scrolling sections. Tabs swap the panel in place. */}
      <section
        aria-label="More about DemandIQ"
        className="relative z-10 shrink-0 border-t border-slate-200 bg-slate-50"
        onPointerEnter={() => setHovering(true)}
        onPointerLeave={() => setHovering(false)}>
        <h2 className="sr-only">Why DemandIQ</h2>
        <div className={container}>
          <div role="tablist" aria-label="Sections" className="flex gap-1 overflow-x-auto sm:gap-6">
            {tabs.map((t) => {
              const active = t.id === tab;
              return (
                <button
                  key={t.id}
                  role="tab"
                  type="button"
                  id={`tab-${t.id}`}
                  aria-selected={active}
                  aria-controls="dock-panel"
                  onClick={() => choose(t.id)}
                  className={[
                    'relative whitespace-nowrap rounded-md px-2 py-3 text-body-sm font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:px-1',
                    active ? 'text-brand-700' : 'text-slate-500 hover:text-slate-800'
                  ].join(' ')}>
                  {t.label}
                  {active && (
                    <>
                      <span aria-hidden="true" className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-slate-200 sm:inset-x-0" />
                      {autoplay && !reduceMotion ? (
                        <motion.span
                          key={`${t.id}-${running}`}
                          aria-hidden="true"
                          className="absolute inset-x-1 bottom-0 h-0.5 origin-left rounded-full bg-brand-600 sm:inset-x-0"
                          initial={{ scaleX: 0 }}
                          animate={{ scaleX: running ? 1 : 0 }}
                          transition={running ? { duration: AUTO_MS / 1000, ease: 'linear' } : { duration: 0.15 }}
                          onAnimationComplete={() => running && advance()}
                        />
                      ) : (
                        <motion.span
                          layoutId="dock-underline"
                          aria-hidden="true"
                          className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-brand-600 sm:inset-x-0"
                          transition={{ duration: 0.25, ease: EASE }}
                        />
                      )}
                    </>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className={`${container} pb-4 pt-3`}>
          <div id="dock-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="min-h-[148px] sm:min-h-[104px]">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={tab}
                initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -6 }}
                transition={{ duration: 0.2, ease: EASE }}>
                {tab === 'features' && (
                  <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-6">
                    {landingFeatures.map((feature, i) => {
                      const Icon = featureIcon[i] ?? TrendingUpIcon;
                      return (
                        <li key={feature.id} className="flex items-start gap-3">
                          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50">
                            <Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                          </span>
                          <div className="min-w-0">
                            <h3 className="text-body-sm font-semibold text-slate-900">{feature.title}</h3>
                            <p className="mt-0.5 line-clamp-2 text-caption leading-snug text-slate-600 sm:line-clamp-3">
                              {feature.description}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {tab === 'how' && (
                  <ol className="grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-6">
                    {landingSteps.map((step, i) => (
                      <li key={step.id} className="flex items-start gap-3">
                        <span className="text-h3 font-semibold tabular-nums leading-none text-brand-700">
                          0{i + 1}
                        </span>
                        <div className="min-w-0">
                          <h3 className="text-body-sm font-semibold text-slate-900">{step.title}</h3>
                          <p className="mt-0.5 line-clamp-2 text-caption leading-snug text-slate-600 sm:line-clamp-3">
                            {step.description}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}

                {tab === 'evidence' && (
                  <ul className="grid grid-cols-2 gap-x-6 gap-y-3 lg:grid-cols-4">
                    {evidencePoints.map((p) => (
                      <li key={p.id} className="flex min-w-0 flex-col">
                        <span className="text-stat font-semibold tabular-nums leading-none text-slate-900">
                          {p.value}
                        </span>
                        <span className="mt-1 line-clamp-2 text-caption leading-snug text-slate-600">
                          {p.label}
                        </span>
                        <span className="mt-0.5 truncate text-caption text-slate-500">{p.source}</span>
                      </li>
                    ))}
                  </ul>
                )}

                {tab === 'community' && (
                  <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-[1.2fr_1fr_1fr] sm:gap-6">
                    <div>
                      <h3 className="text-body-sm font-semibold text-slate-900">
                        Kitchens, canteens and fresh-food vendors across Kigali
                      </h3>
                      <p className="mt-0.5 line-clamp-2 text-caption leading-snug text-slate-600">
                        Built for teams who want to cut waste with data-driven prep decisions.
                      </p>
                    </div>
                    {[
                      { Icon: CalendarCheckIcon, title: 'Per location', text: 'each site forecast on its own' },
                      { Icon: LeafIcon, title: 'Always learning', text: 'updates as new data arrives' }
                    ].map(({ Icon, title, text }) => (
                      <div key={title} className="flex items-center gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50">
                          <Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.9} aria-hidden="true" />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-body-sm font-semibold text-slate-900">{title}</span>
                          <span className="block text-caption text-slate-500">{text}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>

        {/* Legal strip */}
        <div className="border-t border-slate-200">
          <div className={`${container} flex flex-wrap items-center justify-between gap-x-4 gap-y-0 py-1 text-caption text-slate-500`}>
            <span>© {new Date().getFullYear()} DemandIQ . V1</span>
            <span className="flex items-center gap-x-4">
              {[
                { label: 'Privacy & Terms', id: 'privacy' as const }
              ].map((item) => (
                <button key={item.id} type="button" onClick={() => openLegal(item.id)} className={footerLink}>
                  {item.label}
                </button>
              ))}
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}