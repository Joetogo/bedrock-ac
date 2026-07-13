'use client';
import { useEffect, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

type Word = { w: string; l: string };

// "Thinking" across human and invented tongues. Accuracy is cheerfully
// approximate — the point is delight while the agent works, not linguistics.
const WORDS: Word[] = [
  { w: 'thinking', l: 'English' },
  { w: 'whakaaro', l: 'te reo Māori' },
  { w: 'cogito', l: 'Latin' },
  { w: 'Qub', l: 'Klingon' },
  { w: 'nautha', l: 'Sindarin' },
  { w: 'sana', l: 'Quenya' },
  { w: 'cthia', l: 'Vulcan' },
  { w: 'kangaeru', l: '日本語' },
  { w: 'pensando', l: 'Español' },
  { w: 'réfléchir', l: 'Français' },
  { w: 'nachdenken', l: 'Deutsch' },
  { w: 'grishúk', l: 'Orcish' },
  { w: 'ruminating', l: 'English' },
  { w: 'meditor', l: 'Latin' },
];

const CYCLE_MS = 1700;
const RAYS = 8;

// A small rotating starburst — a nod to the Claude mark. Rays radiate from
// center; the whole thing spins slowly and the rays breathe in and out.
function Starburst({ animate }: { animate: boolean }) {
  return (
    <motion.svg
      width="18"
      height="18"
      viewBox="-12 -12 24 24"
      className="shrink-0 text-accent"
      aria-hidden="true"
      style={{ transformOrigin: 'center' }}
      animate={animate ? { rotate: 360 } : undefined}
      transition={animate ? { repeat: Infinity, duration: 6, ease: 'linear' } : undefined}
    >
      <motion.g
        style={{ transformOrigin: 'center' }}
        animate={animate ? { scale: [1, 0.8, 1], opacity: [1, 0.65, 1] } : undefined}
        transition={animate ? { repeat: Infinity, duration: 1.7, ease: 'easeInOut' } : undefined}
      >
        {Array.from({ length: RAYS }).map((_, i) => (
          <line
            key={i}
            x1="0" y1="0" x2="0" y2="-9"
            stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
            transform={`rotate(${(360 / RAYS) * i})`}
          />
        ))}
      </motion.g>
    </motion.svg>
  );
}

function fmt(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function ThinkingIndicator({ startIndex }: { startIndex?: number }) {
  const reduce = useReducedMotion();
  // Randomize the starting word so refreshes feel alive; tests pin it via prop.
  const [i, setI] = useState(() => startIndex ?? Math.floor(Math.random() * WORDS.length));
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    const word = setInterval(() => setI((n) => (n + 1) % WORDS.length), CYCLE_MS);
    const clock = setInterval(() => setSecs((n) => n + 1), 1000);
    return () => { clearInterval(word); clearInterval(clock); };
  }, []);

  const { w, l } = WORDS[i];
  return (
    <div className="ngb-thinking" role="status" aria-label="The agent is thinking">
      <Starburst animate={!reduce} />
      {/* The cycling word is decorative flourish — hidden from screen readers,
          which get the stable "thinking" label above instead of a word-storm. */}
      <motion.span
        key={reduce ? 'static' : `${w}-${i}`}
        initial={reduce ? false : { opacity: 0, y: 3 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className="tword"
        aria-hidden="true"
      >
        <span className="w">{w}</span>
        <span className="l">{l}</span>
      </motion.span>
      <span className="elapsed" aria-hidden="true">{fmt(secs)}</span>
    </div>
  );
}
