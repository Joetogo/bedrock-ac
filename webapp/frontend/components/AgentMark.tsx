'use client';
import { motion, useReducedMotion } from 'framer-motion';

const RAYS = 8;

// The agent's mark: an 8-ray starburst (a nod to the Claude logo) that spins
// slowly and breathes. Used at hero size inside the pulsing ring, and small in
// panel headers. Animation via framer-motion; the outer pulse rings are CSS.
export function AgentMark({ size = 26, animate = true }: { size?: number; animate?: boolean }) {
  const reduce = useReducedMotion();
  const on = animate && !reduce;
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="-12 -12 24 24"
      aria-hidden="true"
      style={{ transformOrigin: 'center' }}
      animate={on ? { rotate: 360 } : undefined}
      transition={on ? { repeat: Infinity, duration: 7, ease: 'linear' } : undefined}
    >
      <motion.g
        style={{ transformOrigin: 'center' }}
        animate={on ? { scale: [1, 0.78, 1], opacity: [1, 0.65, 1] } : undefined}
        transition={on ? { repeat: Infinity, duration: 1.9, ease: 'easeInOut' } : undefined}
      >
        {Array.from({ length: RAYS }).map((_, i) => (
          <line
            key={i}
            x1="0" y1="0" x2="0" y2="-9"
            stroke="#fff" strokeWidth="2.4" strokeLinecap="round"
            transform={`rotate(${(360 / RAYS) * i})`}
          />
        ))}
      </motion.g>
    </motion.svg>
  );
}
