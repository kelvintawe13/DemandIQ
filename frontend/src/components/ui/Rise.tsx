import { motion, useReducedMotion } from 'framer-motion';
import { EASE_OUT_EXPO } from '../../lib/motion';

interface RiseProps {
  children: React.ReactNode;
  /** Seconds to wait before entering — use for stagger across siblings. */
  delay?: number;
  /** How far to rise from, in px. */
  y?: number;
  className?: string;
}

/**
 * A block of UI that fades up on mount.
 *
 * The one-line way to choreograph a screen: wrap sections in `Rise` with
 * increasing delays. Honours `prefers-reduced-motion` by rendering static.
 */
export function Rise({ children, delay = 0, y = 12, className }: RiseProps) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduced ? false : { opacity: 0, y }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: EASE_OUT_EXPO }}
    >
      {children}
    </motion.div>
  );
}
