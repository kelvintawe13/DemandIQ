/**
 * The app's shared motion vocabulary (DESIGN.md § Motion).
 *
 * One module so every surface animates with the same feel: panels rise,
 * lists stagger, modals spring. Callers should pair these with
 * `useReducedMotion()` (or the `Rise` component, which does it for you) so
 * `prefers-reduced-motion` is honoured everywhere.
 */
import type { Transition, Variants } from 'framer-motion';

/** Fast-out, soft-settle — the house ease for UI entrances. */
export const EASE_OUT_EXPO: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** Fade-up used for blocks of content (forms, panels, cards). */
export const fadeRise: Variants = {
  hidden: { opacity: 0, y: 10 },
  shown: { opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE_OUT_EXPO } },
};

/** Parent config for staggered children — pair with `fadeRise` children. */
export const staggerParent = (stagger = 0.07, delay = 0): Variants => ({
  hidden: {},
  shown: { transition: { staggerChildren: stagger, delayChildren: delay } },
});

/** Quieter variant for list rows — many rows must not feel heavy. */
export const listRow: Variants = {
  hidden: { opacity: 0, y: 6 },
  shown: { opacity: 1, y: 0, transition: { duration: 0.3, ease: EASE_OUT_EXPO } },
};

/** Soft spring for interactive surfaces (modals, sheets, expanders). */
export const SPRING_SOFT: Transition = { type: 'spring', stiffness: 320, damping: 28 };
