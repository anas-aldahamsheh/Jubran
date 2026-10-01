/**
 * The motion language of the app: one family of easings and springs, used everywhere.
 * - Entrances ease out (content arrives and settles), exits are short.
 * - Things the guest touches (sheets, buttons, chips) move on springs.
 * MotionConfig (reducedMotion="user") turns movement into simple fades for guests
 * who ask their device for less motion.
 */
import type { Transition, Variants } from "motion/react";

export const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];
export const EASE_EMPHASIZED: [number, number, number, number] = [0.2, 0, 0, 1];
export const EASE_IN: [number, number, number, number] = [0.4, 0, 1, 1];

export const spring = {
  /** Buttons, chips, badges: quick and firm. */
  snappy: { type: "spring", stiffness: 520, damping: 34, mass: 0.8 } satisfies Transition,
  /** Cards, panels, layout changes. */
  smooth: { type: "spring", stiffness: 300, damping: 32 } satisfies Transition,
  /** Sheets and big surfaces: a little weight. */
  sheet: { type: "spring", stiffness: 380, damping: 38, mass: 0.9 } satisfies Transition,
  /** Playful pops (added to the basket, a new badge). */
  pop: { type: "spring", stiffness: 600, damping: 18 } satisfies Transition,
} as const;

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_OUT } },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.35, ease: EASE_OUT } },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  show: { opacity: 1, scale: 1, transition: { duration: 0.35, ease: EASE_OUT } },
};

/** A parent that reveals its children one after another (grouping by rhythm). */
export function stagger(delayChildren = 0.04, staggerChildren = 0.045): Variants {
  return { hidden: {}, show: { transition: { delayChildren, staggerChildren } } };
}
