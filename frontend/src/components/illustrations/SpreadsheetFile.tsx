
export function SpreadsheetFile({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 140 150" className={className} fill="none" aria-hidden="true">
      {/* page */}
      <path
        d="M22 6h72l26 26v112a4 4 0 0 1-4 4H22a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4z"
        fill="#ffffff"
        stroke="#cbd5e1"
        strokeWidth="2" />
      
      <path d="M94 6l26 26H98a4 4 0 0 1-4-4V6z" fill="#e2e8f0" />

      {/* grid rows */}
      <g stroke="#e2e8f0" strokeWidth="2">
        <path d="M32 52h76M32 68h76M32 84h76" />
        <path d="M60 44v48M84 44v48" />
      </g>

      {/* trend line */}
      <path
        d="M34 128l16-16 14 10 18-24 14 12 12-18"
        stroke="#16a34a"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round" />
      

      {/* excel badge */}
      <rect x="6" y="26" width="36" height="36" rx="5" fill="#16794a" />
      <path
        d="M17 36l14 16M31 36L17 52"
        stroke="#ffffff"
        strokeWidth="3.4"
        strokeLinecap="round" />
      
    </svg>);

}