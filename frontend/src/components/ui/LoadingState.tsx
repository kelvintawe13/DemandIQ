interface LoadingStateProps {
  /** Accessible name for the spinner, e.g. "Loading users". */
  label: string;
  /** `md` is a full content region; `sm` sits inside a panel or card. */
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * The one loading indicator in the app. Twenty-odd pages had copied the same
 * fifteen-class spinner markup, with the only differences being padding and
 * whether an aria-label was remembered.
 */
export function LoadingState({ label, size = 'md', className = '' }: LoadingStateProps) {
  const spinner = size === 'sm' ? 'h-8 w-8' : 'h-10 w-10';
  return (
    <div
      className={[
        'flex items-center justify-center',
        size === 'sm' ? 'py-12' : 'py-16',
        className,
      ].join(' ')}
    >
      <Spinner label={label} className={spinner} />
    </div>
  );
}

/* Ring colours in use: the default brand arc, a muted track on a tinted panel,
   and the light-on-dark arc inside a filled button. */
const ringToneClass = {
  brand: 'border-slate-200 border-t-brand-600',
  muted: 'border-slate-300 border-t-brand-600',
  onDark: 'border-white/30 border-t-white',
} as const;

interface SpinnerProps {
  /** Accessible name. Omit for a decorative spinner, which is then `aria-hidden`. */
  label?: string;
  /** Size utilities, e.g. `h-6 w-6`. Defaults to `h-8 w-8`. */
  className?: string;
  tone?: keyof typeof ringToneClass;
}

/**
 * The spinner on its own, for the places that wait inside a button, a row or a
 * panel and cannot take `LoadingState`'s centred padding. Same recipe, so the
 * size or a new ring colour changes in one file instead of twelve.
 */
export function Spinner({ label, className = 'h-8 w-8', tone = 'brand' }: SpinnerProps) {
  return (
    <span
      {...(label ? { role: 'status', 'aria-label': label } : { 'aria-hidden': true })}
      className={`inline-block animate-spin rounded-full border-2 ${ringToneClass[tone]} ${className}`}
    />
  );
}
