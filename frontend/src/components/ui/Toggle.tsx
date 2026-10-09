interface ToggleProps {
  checked: boolean;
  /** Called with the next value, so callers never have to invert it themselves. */
  onChange: (next: boolean) => void;
  /** Accessible name for the switch. */
  label: string;
  disabled?: boolean;
  /** Why the switch is locked; shown on hover. Defaults to a seat message. */
  title?: string;
  className?: string;
}

/**
 * The one switch in the product.
 *
 * Before this, four screens hand-rolled the same `role="switch"` markup with
 * two different sizes, and Settings kept its own local copy. They all measured
 * 20-24px tall, which is comfortable with a mouse and awkward with a thumb — a
 * phone user aiming at a rule toggle gets the row next to it.
 *
 * The track is an inner element and the button around it is transparent: that
 * lets the button carry vertical padding for a 44px touch target while the
 * visible switch stays its designed size, and the matching negative margin
 * means the extra height does not push anything around.
 */
export function Toggle({ checked, onChange, label, disabled, title, className = '' }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      title={title ?? (disabled ? `${label} cannot be changed by your seat` : undefined)}
      onClick={() => onChange(!checked)}
      className={[
        'relative -my-2.5 inline-flex shrink-0 items-center rounded-full py-2.5 transition-[box-shadow,opacity] duration-200 ease-out',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      ].join(' ')}
    >
      <span
        className={[
          'relative h-6 w-11 rounded-full transition-colors duration-200 ease-out',
          checked ? 'bg-brand-600' : 'bg-slate-300',
        ].join(' ')}
      >
        <span
          className={[
            'absolute left-0.5 top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
            checked ? 'translate-x-5' : 'translate-x-0',
          ].join(' ')}
        />
      </span>
    </button>
  );
}
