
const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const
};

/** Sources — plug connecting into a hub of channels. */
export function ConnectSourcesArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <circle cx="100" cy="66" r="26" />
      <path d="M92 60l8 12 8-12" strokeWidth={2} />
      <path d="M100 40V22M100 92v18M74 66H48M126 66h26" strokeDasharray="3 7" strokeWidth={1.9} />
      <rect x="76" y="6" width="48" height="18" rx="5" strokeWidth={2} />
      <rect x="14" y="54" width="34" height="26" rx="5" strokeWidth={2} />
      <rect x="152" y="54" width="34" height="26" rx="5" strokeWidth={2} />
      <rect x="76" y="108" width="48" height="18" rx="5" strokeWidth={2} />
      <path d="M24 64h14M24 70h10M162 64h14M162 70h10" strokeWidth={1.8} />
    </svg>);

}

/** Reports — stacked documents with a chart page on top. */
export function ReportsArt({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 200 130" className={className} {...base} aria-hidden="true">
      <rect x="44" y="18" width="92" height="104" rx="8" />
      <rect x="30" y="10" width="92" height="104" rx="8" fill="#ffffff" />
      <rect x="30" y="10" width="92" height="104" rx="8" />
      <path d="M46 32h40M46 44h60" strokeWidth={2} />
      <g strokeWidth={2}>
        <rect x="46" y="62" width="12" height="34" rx="2" />
        <rect x="64" y="52" width="12" height="44" rx="2" />
        <rect x="82" y="72" width="12" height="24" rx="2" />
      </g>
      <path d="M46 96h60" strokeWidth={2} />
      <circle cx="158" cy="86" r="22" />
      <path d="M158 64v22h22" strokeWidth={2} />
    </svg>);

}

