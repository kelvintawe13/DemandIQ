
export function CameraNotebook({ className }: {className?: string;}) {
  return (
    <svg
      viewBox="0 0 200 130"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true">
      
      {/* sparkle strokes */}
      <path d="M46 22 L40 10" />
      <path d="M68 14 L66 2" />
      <path d="M90 20 L96 9" />
      <path d="M26 40 L14 33" />

      {/* camera body */}
      <path d="M18 52 h26 l8-10h26l8 10h12a8 8 0 0 1 8 8v44a8 8 0 0 1-8 8H18a8 8 0 0 1-8-8V60a8 8 0 0 1 8-8z" />
      {/* lens */}
      <circle cx="59" cy="82" r="20" />
      <circle cx="59" cy="82" r="11" />
      <circle cx="91" cy="61" r="3" fill="currentColor" stroke="none" />

      {/* open book */}
      <path d="M108 44c12-7 24-8 36-4v72c-12-4-24-3-36 4V44z" />
      <path d="M144 40c12-4 24-3 36 4v72c-12-7-24-8-36-4" />
      <path d="M144 40v72" />
      <path d="M118 62h18M118 76h18M118 90h12" strokeWidth={2} />
      <path d="M154 62h16M154 76h16M154 90h11" strokeWidth={2} />
    </svg>);

}