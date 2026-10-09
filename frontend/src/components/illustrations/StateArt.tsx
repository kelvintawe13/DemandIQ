
const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const
};

/** Nothing here yet — an open tray waiting for its first rows. */
export function EmptyTrayArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <path d="M26 58l14-34h120l14 34" />
      <path d="M26 58v42a8 8 0 008 8h132a8 8 0 008-8V58z" />
      <path d="M26 58h40l8 14h52l8-14h40" />
      <path d="M100 16V4M74 20l-6-9M126 20l6-9" strokeWidth={1.9} strokeDasharray="2 6" />
    </svg>);

}

/** No results — magnifier over an empty list. */
export function NoResultsArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <rect x="30" y="16" width="104" height="98" rx="9" />
      <path d="M48 42h44M48 60h30M48 78h38" strokeWidth={1.9} strokeDasharray="3 7" />
      <circle cx="140" cy="76" r="26" />
      <path d="M159 95l14 14" />
    </svg>);

}

/** Something broke — an unplugged connector. */
export function BrokenPlugArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <path d="M14 65h34" />
      <rect x="48" y="45" width="34" height="40" rx="9" />
      <path d="M118 45h34v40h-34" />
      <path d="M152 65h34" />
      <path d="M118 52v26" />
      <path d="M92 40l-8 16h14l-8 16" strokeWidth={2.4} />
      <path d="M62 30V18M68 100v12" strokeWidth={1.8} strokeDasharray="2 6" />
    </svg>);

}

/** Route not found — a signpost pointing nowhere. */
export function NotFoundArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <path d="M100 118V30" />
      <path d="M100 38h56l12 13-12 13h-56z" />
      <path d="M100 74H44L32 87l12 13h56z" />
      <path d="M78 118h44" />
      <circle cx="100" cy="20" r="9" strokeWidth={2} />
      <path d="M22 30h10M168 96h10" strokeWidth={1.8} strokeDasharray="2 6" />
    </svg>);

}

/** Offline / waiting on a connection. */
export function OfflineArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <path d="M40 58a84 84 0 01120 0" strokeDasharray="4 8" />
      <path d="M62 80a52 52 0 0176 0" strokeDasharray="4 8" />
      <path d="M84 100a22 22 0 0132 0" />
      <circle cx="100" cy="114" r="4" strokeWidth={3} />
      <path d="M150 24l28 28M178 24l-28 28" strokeWidth={2.4} />
    </svg>);

}
