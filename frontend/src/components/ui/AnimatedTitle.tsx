import { motion, useReducedMotion } from 'framer-motion';
import type { ElementType, ReactNode } from 'react';

type AnimatedTitleProps = {
  children: ReactNode;
  /** HTML tag — defaults to h1. */
  as?: 'h1' | 'h2' | 'h3' | 'p' | 'span';
  className?: string;
  /**
   * `rise` — whole line fades up (default, calm).
   * `words` — each word rises in sequence (hero / welcome).
   */
  variant?: 'rise' | 'words';
  /** Extra delay before the animation starts (seconds). */
  delay?: number;
};

const ease = [0.23, 1, 0.32, 1] as const;

/**
 * Inter display titles with a short entrance motion.
 * Honours `prefers-reduced-motion` by rendering static text.
 */
export function AnimatedTitle({
  children,
  as = 'h1',
  className = '',
  variant = 'rise',
  delay = 0,
}: AnimatedTitleProps) {
  const reduce = useReducedMotion();
  const Tag = as as ElementType;

  if (reduce || typeof children !== 'string') {
    return <Tag className={['font-display', className].filter(Boolean).join(' ')}>{children}</Tag>;
  }

  if (variant === 'words') {
    const words = children.trim().split(/\s+/);
    return (
      <Tag className={['font-display', className].filter(Boolean).join(' ')} aria-label={children}>
        {words.map((word, index) => (
          <motion.span
            key={`${word}-${index}`}
            className="inline-block will-change-transform"
            initial={{ opacity: 0, y: '0.45em' }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, ease, delay: delay + index * 0.055 }}
          >
            {word}
            {index < words.length - 1 ? '\u00A0' : null}
          </motion.span>
        ))}
      </Tag>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease, delay }}
    >
      <Tag className={['font-display', className].filter(Boolean).join(' ')}>{children}</Tag>
    </motion.div>
  );
}
