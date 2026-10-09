
export function PosTerminal({ className }: {className?: string;}) {
  return (
    <svg viewBox="0 0 260 150" className={className} fill="none" aria-hidden="true">
      {/* receipt */}
      <path
        d="M44 12h48v24H44z"
        fill="#ffffff"
        stroke="#94a3b8"
        strokeWidth="2"
        strokeLinejoin="round" />
      
      <path d="M52 20h30M52 28h22" stroke="#cbd5e1" strokeWidth="2" strokeLinecap="round" />

      {/* pos terminal */}
      <rect x="30" y="34" width="76" height="106" rx="10" fill="#1e293b" />
      <rect x="38" y="44" width="60" height="38" rx="4" fill="#0f6d67" />
      <g fill="#6fd6c6">
        <rect x="46" y="66" width="7" height="10" rx="1.5" />
        <rect x="57" y="60" width="7" height="16" rx="1.5" />
        <rect x="68" y="54" width="7" height="22" rx="1.5" />
        <rect x="79" y="48" width="7" height="28" rx="1.5" />
      </g>
      <g fill="#475569">
        <rect x="40" y="90" width="16" height="10" rx="2" />
        <rect x="60" y="90" width="16" height="10" rx="2" />
        <rect x="80" y="90" width="16" height="10" rx="2" />
        <rect x="40" y="104" width="16" height="10" rx="2" />
        <rect x="60" y="104" width="16" height="10" rx="2" />
        <rect x="80" y="104" width="16" height="10" rx="2" />
      </g>
      <rect x="40" y="120" width="22" height="10" rx="2" fill="#ef4444" />
      <rect x="66" y="120" width="14" height="10" rx="2" fill="#f59e0b" />
      <rect x="84" y="120" width="12" height="10" rx="2" fill="#22c55e" />

      {/* dotted transfer */}
      <path
        d="M112 86c14-14 26 10 40-6"
        stroke="#94a3b8"
        strokeWidth="2"
        strokeDasharray="4 5"
        strokeLinecap="round" />
      
      <path
        d="M152 74l8 6-8 6"
        stroke="#94a3b8"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round" />
      

      {/* sparkle */}
      <path
        d="M132 44l3.5 8 8 3.5-8 3.5-3.5 8-3.5-8-8-3.5 8-3.5z"
        fill="#17a294" />
      

      {/* phone */}
      <rect x="172" y="20" width="62" height="118" rx="12" fill="#1e293b" />
      <rect x="178" y="28" width="50" height="102" rx="7" fill="#ffffff" />
      <rect x="192" y="24" width="22" height="4" rx="2" fill="#334155" />
      <rect x="186" y="62" width="34" height="18" rx="4" fill="#fbbf24" />
      <path d="M194 66l6 10 6-10" stroke="#111827" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M186 92h34M186 102h24"
        stroke="#e2e8f0"
        strokeWidth="3"
        strokeLinecap="round" />
      
    </svg>);

}