
interface BrandLogoProps {
  size?: 'sm' | 'md';
  align?: 'left' | 'center';
}

export function BrandLogo({ size = 'md', align = 'left' }: BrandLogoProps) {
  const isSmall = size === 'sm';
  return (
    <span
      className={[
      'flex items-center gap-2',
      align === 'center' ? 'justify-center' : 'justify-start'].
      join(' ')}>
      
      <svg
        viewBox="0 0 28 28"
        className={isSmall ? 'h-6 w-6' : 'h-7 w-7'}
        aria-hidden="true"
        fill="none">
        
        <rect x="2" y="15" width="5" height="11" rx="1.5" fill="#0f6d67" />
        <rect x="9.5" y="10" width="5" height="16" rx="1.5" fill="#17a294" />
        <rect x="17" y="5" width="5" height="21" rx="1.5" fill="#6fd6c6" />
        <path
          d="M6 12L13 6l5 4 7-7"
          stroke="#0f6d67"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round" />
        
        <path
          d="M21 3h5v5"
          stroke="#0f6d67"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round" />
        
      </svg>
      <span
        className={[
        'font-bold tracking-tight text-slate-900',
        isSmall ? 'text-lead' : 'text-h3'].
        join(' ')}>
        
        Demand<span className="text-brand-700">IQ</span>
      </span>
    </span>);

}