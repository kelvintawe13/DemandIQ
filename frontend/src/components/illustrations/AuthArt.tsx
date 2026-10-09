
interface ArtProps {
  className?: string;
}

const strokeProps = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const
};

/** Sign in: analytics dashboard with rising trend. */
export function DashboardArt({ className }: ArtProps) {
  return (
    <svg viewBox="0 0 240 190" className={className} {...strokeProps} aria-hidden="true">
      <rect x="10" y="16" width="190" height="130" rx="10" />
      <path d="M10 42h190" />
      <circle cx="26" cy="29" r="3" />
      <circle cx="38" cy="29" r="3" />
      <path d="M26 62h58M26 74h38" strokeWidth={2} />
      <path d="M26 96l24-20 20 14 30-38" />
      <path d="M92 52h16v16" />
      <g strokeWidth={2}>
        <rect x="26" y="112" width="14" height="20" rx="2" />
        <rect x="48" y="102" width="14" height="30" rx="2" />
        <rect x="70" y="118" width="14" height="14" rx="2" />
      </g>
      <rect x="120" y="60" width="66" height="72" rx="8" />
      <circle cx="153" cy="90" r="18" />
      <path d="M153 72v18h18" />
      <path d="M134 116h38" strokeWidth={2} />

      {/* floating badge: translucent so it reads on both light and dark panels */}
      <rect x="164" y="132" width="62" height="42" rx="10" fill="currentColor" fillOpacity="0.12" />
      <path d="M176 158l10-10 8 6 12-14" />
      <path d="M200 140h8v8" />

      {/* accents */}
      <g strokeWidth={1.8}>
        <path d="M216 26l2.6 6 6 2.6-6 2.6-2.6 6-2.6-6-6-2.6 6-2.6z" />
        <path d="M18 168l2.2 5 5 2.2-5 2.2-2.2 5-2.2-5-5-2.2 5-2.2z" />
      </g>
    </svg>);

}

/** Sign up: kitchen prep with growth chart. */
export function KitchenGrowthArt({ className }: ArtProps) {
  return (
    <svg viewBox="0 0 240 190" className={className} {...strokeProps} aria-hidden="true">
      {/* chart */}
      <path d="M22 96V22" strokeWidth={2} />
      <path d="M22 96h84" strokeWidth={2} />
      <g strokeWidth={2}>
        <rect x="32" y="70" width="12" height="26" rx="2" />
        <rect x="50" y="56" width="12" height="40" rx="2" />
        <rect x="68" y="40" width="12" height="56" rx="2" />
        <rect x="86" y="28" width="12" height="68" rx="2" />
      </g>
      <path d="M30 62l18-16 16 10 26-30" />
      <path d="M78 26h14v14" />

      {/* pot */}
      <path d="M126 70h58v34a10 10 0 0 1-10 10h-38a10 10 0 0 1-10-10z" />
      <path d="M118 78h8M184 78h8" />
      <path d="M140 60c0-8 8-8 8-16M158 60c0-8 8-8 8-16" strokeWidth={1.9} />

      {/* counter */}
      <path d="M108 118h112v50H108z" />
      <path d="M108 134h112M150 134v34M188 134v34" strokeWidth={2} />

      {/* plant */}
      <path d="M40 132c-10-12-10-26 4-34 8 10 8 24-4 34z" />
      <path d="M52 132c10-12 10-26-4-34-8 10-8 24 4 34z" />
      <path d="M32 134h30l-4 34H36z" />

      {/* leaf badge */}
      <circle cx="98" cy="164" r="14" strokeWidth={1.9} />
      <path d="M98 156c6 3 7 11 0 15-6-4-7-12 0-15z" strokeWidth={1.9} />
    </svg>);

}

/** Reset password + check email: envelope with key. */
export function MailKeyArt({ className }: ArtProps) {
  return (
    <svg viewBox="0 0 240 190" className={className} {...strokeProps} aria-hidden="true">
      <rect x="44" y="66" width="152" height="98" rx="10" />
      <path d="M44 76l76 52 76-52" />
      <path d="M44 156l56-42M196 156l-56-42" strokeWidth={1.9} />

      {/* magnifier with lock */}
      <circle cx="120" cy="46" r="30" fill="#ffffff" />
      <circle cx="120" cy="46" r="30" />
      <path d="M99 68l-18 20" strokeWidth={5} />
      <rect x="110" y="42" width="20" height="17" rx="4" strokeWidth={2} />
      <path d="M114 42v-6a6 6 0 0 1 12 0v6" strokeWidth={2} />

      {/* dotted arc */}
      <path d="M30 60C48 16 152 2 208 44" strokeDasharray="3 8" strokeWidth={1.8} />

      {/* small bars */}
      <g strokeWidth={1.9}>
        <rect x="14" y="128" width="12" height="28" rx="2" />
        <rect x="30" y="112" width="12" height="44" rx="2" />
        <rect x="200" y="120" width="12" height="36" rx="2" />
        <rect x="216" y="104" width="12" height="52" rx="2" />
      </g>
    </svg>);

}

/** Verify email: shield with code. */
export function ShieldCodeArt({ className }: ArtProps) {
  return (
    <svg viewBox="0 0 240 190" className={className} {...strokeProps} aria-hidden="true">
      <path d="M120 16l58 20v46c0 42-24 72-58 92-34-20-58-50-58-92V36z" />
      <path d="M96 92h48v30H96z" strokeWidth={2} />
      <path d="M106 92V80a14 14 0 0 1 28 0v12" strokeWidth={2} />
      <circle cx="120" cy="106" r="4" strokeWidth={2} />

      {/* code chips */}
      <g strokeWidth={2}>
        <rect x="14" y="60" width="26" height="32" rx="5" />
        <rect x="14" y="104" width="26" height="32" rx="5" />
        <rect x="200" y="60" width="26" height="32" rx="5" />
        <rect x="200" y="104" width="26" height="32" rx="5" />
      </g>
      <path d="M22 76h10M22 120h10M208 76h10M208 120h10" strokeWidth={2} />

      {/* connecting dots */}
      <path d="M40 76h22M40 120h22M178 76h22M178 120h22" strokeDasharray="3 7" strokeWidth={1.8} />
    </svg>);

}

/** All set: success check with data cards. */
export function SuccessCheckArt({ className }: ArtProps) {
  return (
    <svg viewBox="0 0 240 190" className={className} {...strokeProps} aria-hidden="true">
      <g strokeWidth={2}>
        <rect x="12" y="104" width="16" height="48" rx="3" />
        <rect x="34" y="86" width="16" height="66" rx="3" />
        <rect x="56" y="66" width="16" height="86" rx="3" />
      </g>
      <path d="M16 84l20-16 18 10 22-26" strokeWidth={2} />

      <g strokeWidth={2.6}>
        <path d="M120 22V8" />
        <path d="M158 34l8-12" />
        <path d="M82 34l-8-12" />
        <path d="M176 64l13-7" />
        <path d="M64 64l-13-7" />
        <path d="M120 150v14" />
        <path d="M158 138l8 12" />
        <path d="M82 138l-8 12" />
      </g>

      <circle cx="120" cy="86" r="40" fill="currentColor" stroke="none" />
      <path
        d="M102 87l13 13 25-27"
        stroke="#ffffff"
        strokeWidth={7}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round" />
      

      <rect x="176" y="86" width="56" height="60" rx="8" strokeWidth={2} />
      <circle cx="196" cy="108" r="11" strokeWidth={2} />
      <path d="M196 97v11h11" strokeWidth={2} />
      <path d="M186 128h36M186 138h24" strokeWidth={2} />
    </svg>);

}