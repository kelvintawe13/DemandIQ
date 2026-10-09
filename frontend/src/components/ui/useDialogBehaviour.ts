import { useEffect, useRef } from 'react';

/**
 * The dialog contract `Modal` provides, for the few surfaces that own their own
 * layout and so cannot render through `Modal` itself (the legal reader, the
 * welcome sheet).
 *
 * Without this they each re-implemented part of it: the legal reader had Escape
 * and the scroll lock but no Tab trap and no focus return, and the welcome sheet
 * declared `aria-modal` with none of it. Returns the ref to put on the panel
 * element, which must also carry `tabIndex={-1}` so it can take focus.
 */
export function useDialogBehaviour(open: boolean, onClose: () => void) {
  const panelRef = useRef<HTMLDivElement>(null);
  // Callers pass an inline arrow for `onClose`, so its identity changes every
  // render. Reading it through a ref keeps the effect tied to `open` alone —
  // otherwise every keystroke re-ran the effect and pulled focus back to the
  // dialog, so a typed field only ever kept its first character.
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  // The control that opened the dialog, so focus can return to it on close.
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const focusable = () =>
      Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeRef.current();
        return;
      }
      // Keep Tab inside the dialog. Without this the focus walks into the page
      // behind the overlay — visually hidden, but still keyboard-live.
      if (event.key !== 'Tab') return;
      const items = focusable();
      const panel = panelRef.current;
      if (items.length === 0) {
        panel?.focus();
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = Boolean(panel && active && panel.contains(active));
      if (event.shiftKey && (active === first || !inside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey);
    // Move focus into the dialog so keyboard users land in the right place.
    panelRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
      // Return focus to the trigger, not to the top of the document.
      previouslyFocused.current?.focus?.();
    };
  }, [open]);

  return panelRef;
}
