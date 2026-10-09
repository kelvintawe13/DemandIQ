import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import type { Variants } from 'framer-motion';

interface RevealProps {
  children: React.ReactNode;
  /** Delay in seconds, used to stagger siblings that aren't in a RevealGroup. */
  delay?: number;
  /** Distance travelled on entry. Small by default — motion should be felt, not watched. */
  y?: number;
  className?: string;
  as?: 'div' | 'section' | 'li' | 'ul' | 'span';
}

const EASE = [0.23, 1, 0.32, 1] as const;

/** Single element that fades and rises into place the first time it is seen. */
export function Reveal({
  children,
  delay = 0,
  y = 14,
  className,
  as = 'div'
}: RevealProps) {
  const reduced = useReducedMotion();
  const Component = motion[as];

  return (
    <Component
      className={className}
      initial={reduced ? false : { opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-60px' }}
      transition={{ duration: 0.28, ease: EASE, delay }}>
      
      {children}
    </Component>);

}

const groupVariants: Variants = {
  hidden: {},
  shown: { transition: { staggerChildren: 0.05, delayChildren: 0.02 } }
};

const itemVariants: Variants = {
  hidden: { opacity: 0, y: 12 },
  shown: { opacity: 1, y: 0, transition: { duration: 0.26, ease: EASE } }
};

/** Container whose RevealItem children arrive one after another. */
export function RevealGroup({
  children,
  className,
  as = 'div'




}: {children: React.ReactNode;className?: string;as?: 'div' | 'ul' | 'section';}) {
  const reduced = useReducedMotion();
  const Component = motion[as];

  return (
    <Component
      className={className}
      variants={groupVariants}
      initial={reduced ? false : 'hidden'}
      whileInView="shown"
      viewport={{ once: true, margin: '-60px' }}>
      
      {children}
    </Component>);

}

export function RevealItem({
  children,
  className,
  as = 'div'




}: {children: React.ReactNode;className?: string;as?: 'div' | 'li';}) {
  const Component = motion[as];
  return (
    <Component className={className} variants={itemVariants}>
      {children}
    </Component>);

}