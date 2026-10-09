export default {content: [
  './index.html',
  './src/**/*.{js,ts,jsx,tsx}'
],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Professional ash/gray color scheme (default)
        brand: {
          50: '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          300: '#cbd5e1',
          400: '#94a3b8',
          500: '#64748b',
          600: '#475569',
          700: '#334155',
          800: '#1e293b',
          900: '#0f172a',
        },
        // Keep green color scheme for theme switching
        green: {
          50: '#effbf8',
          100: '#d6f5ee',
          200: '#a9e9dd',
          300: '#6fd6c6',
          400: '#38bdaa',
          500: '#17a294',
          600: '#0d8379',
          700: '#0f6d67',
          800: '#115a56',
          900: '#124b48',
        },
        // Add blue color scheme for more options
        blue: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
        // Yellow color scheme
        yellow: {
          50: '#fffbeb',
          100: '#fef3c7',
          200: '#fde68a',
          300: '#fcd34d',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
          700: '#b45309',
          800: '#92400e',
          900: '#78350f',
        },
        // Purple color scheme
        purple: {
          50: '#faf5ff',
          100: '#f3e8ff',
          200: '#e9d5ff',
          300: '#d8b4fe',
          400: '#c084fc',
          500: '#a855f7',
          600: '#9333ea',
          700: '#7e22ce',
          800: '#6b21a8',
          900: '#581c87',
        },
        // Orange color scheme
        orange: {
          50: '#fff7ed',
          100: '#ffedd5',
          200: '#fed7aa',
          300: '#fdba74',
          400: '#fb923c',
          500: '#f97316',
          600: '#ea580c',
          700: '#c2410c',
          800: '#9a3412',
          900: '#7c2d12',
        },
        // Red color scheme
        red: {
          50: '#fef2f2',
          100: '#fee2e2',
          200: '#fecaca',
          300: '#fca5a5',
          400: '#f87171',
          500: '#ef4444',
          600: '#dc2626',
          700: '#b91c1c',
          800: '#991b1b',
          900: '#7f1d1d',
        },
        // Pink color scheme
        pink: {
          50: '#fdf2f8',
          100: '#fce7f3',
          200: '#fbcfe8',
          300: '#f9a8d4',
          400: '#f472b6',
          500: '#ec4899',
          600: '#db2777',
          700: '#be185d',
          800: '#9d174d',
          900: '#831843',
        },
        /* Ocean — the default scheme. Generated in OKLCH (hue 208, C 0.94) and
           gated on WCAG AA: 500/600/700 clear 4.5:1 as text on white, and the
           dark fills clear 4.5:1 against white label text. The dark-mode text
           inks live on a separate --brand-ink-* ramp, because a single custom
           property cannot be both a white-text fill and a dark-surface ink. */
        ocean: {
          50: '#e5f7f9',
          100: '#c9f1f8',
          200: '#a1e6f1',
          300: '#68d1e0',
          400: '#28aaba',
          500: '#007d8b',
          600: '#006a76',
          700: '#00545e',
          800: '#004047',
          900: '#002d33',
        },
      },
      /* Inter for both UI and display. One family, 4 weights in use (400/500/
         600/700), and true tabular figures — which matters here because the
         forecast, deviation and KPI tables align digits vertically. The system
         stack stays behind Inter in every list so the font-display:swap frame
         renders in the system face instead of Times. */
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
        display: ['Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'],
        /* Instrument Serif is gone; font-serif stays a real serif for prose
           blocks that want one. Nothing in src/ uses it today. */
        serif: ['ui-serif', 'Georgia', 'Cambria', '"Times New Roman"', 'serif'],
        /* Was previously undefined, so the six font-mono call sites silently
           fell back to Tailwind's default mono stack. */
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      /* Semantic type scale. Named by role, not size, so the rules survive a
         new screen. See DESIGN.md § Typography.

         Every size the app actually renders has a name here. The dense end
         (nano / fine / micro / caption / meta / body-xs / body-sm / control /
         body-sm-lg / body) exists because the app had 1,300-odd `text-[Npx]`
         call sites and 11.5px, 13px and 14px — the three most used — had no
         token at all. The display end (h5 / stat-sm / h3-lg / display-xs /
         stat-md / display-sm / display / display-lg) covers the reported
         title and KPI sizes between h3 and the fluid `h1` / `stat` ramps.

         `fine` and `overline` are both 11px on purpose: `overline` is the
         tracked uppercase eyebrow, `fine` is plain dense print.

         Default Tailwind sizes stay available; these are additive. */
      fontSize: {
        nano: ['9.5px', { lineHeight: '1.4' }],
        fine: ['11px', { lineHeight: '1.45' }],
        overline: ['11px', { lineHeight: '1.4', letterSpacing: '0.16em' }],
        micro: ['11.5px', { lineHeight: '1.45' }],
        caption: ['12px', { lineHeight: '1.45' }],
        meta: ['12.5px', { lineHeight: '1.45' }],
        'body-xs': ['13px', { lineHeight: '1.5' }],
        'body-sm': ['13.5px', { lineHeight: '1.55' }],
        control: ['14px', { lineHeight: '1.5' }],
        'body-sm-lg': ['14.5px', { lineHeight: '1.5' }],
        body: ['15px', { lineHeight: '1.6' }],
        h5: ['16px', { lineHeight: '1.35' }],
        lead: ['17px', { lineHeight: '1.55' }],
        h4: ['18px', { lineHeight: '1.3', letterSpacing: '-0.01em' }],
        'stat-sm': ['19px', { lineHeight: '1.15', letterSpacing: '-0.01em' }],
        h3: ['20px', { lineHeight: '1.25', letterSpacing: '-0.01em' }],
        'h3-lg': ['22px', { lineHeight: '1.2', letterSpacing: '-0.012em' }],
        'display-xs': ['24px', { lineHeight: '1.15', letterSpacing: '-0.015em' }],
        h2: ['26px', { lineHeight: '1.15', letterSpacing: '-0.015em' }],
        'stat-md': ['28px', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
        'display-sm': ['30px', { lineHeight: '1.12', letterSpacing: '-0.018em' }],
        display: ['32px', { lineHeight: '1.08', letterSpacing: '-0.02em' }],
        'h2-lg': ['34px', { lineHeight: '1.12', letterSpacing: '-0.02em' }],
        'display-lg': ['36px', { lineHeight: '1.05', letterSpacing: '-0.02em' }],
        h1: ['clamp(2.5rem, 5.4vw, 4.25rem)', { lineHeight: '1.02', letterSpacing: '-0.028em' }],
        stat: ['clamp(1.75rem, 3vw, 2.25rem)', { lineHeight: '1', letterSpacing: '-0.02em' }],
        'stat-lg': ['clamp(2.5rem, 5vw, 3.5rem)', { lineHeight: '1', letterSpacing: '-0.03em' }],
      },
      /* Layered, tinted elevation. Shadows carry the page hue (a cool navy) so
         depth reads as light on the surface rather than a grey smear. */
      boxShadow: {
        lift: '0 1px 0 rgba(15, 23, 42, 0.04), 0 12px 32px -12px rgba(15, 23, 42, 0.14)',
        'lift-lg': '0 2px 0 rgba(15, 23, 42, 0.03), 0 28px 70px -24px rgba(15, 23, 42, 0.28)',
      },
    },
  },
}
