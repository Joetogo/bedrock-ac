'use client';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { MessageSquare, Trash2 } from 'lucide-react';
import type { Thread } from '@/lib/types';

function relTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? 'yesterday' : `${days}d ago`;
}

export function SessionList({ threads, activeId, onSelect, onDelete }: {
  threads: Thread[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const reduce = useReducedMotion();
  if (threads.length === 0) {
    return <div className="ngb-sessions"><div className="empty">No conversations yet — start one in the Chat tab.</div></div>;
  }
  return (
    <div className="ngb-sessions">
      <AnimatePresence>
        {threads.map((t) => (
          <motion.div
            key={t.id}
            layout={!reduce}
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className={`ngb-session${t.id === activeId ? ' active' : ''}`}
          >
            <span className="ic"><MessageSquare size={15} /></span>
            <button className="open" onClick={() => onSelect(t.id)}>
              <div className="q">{t.title}</div>
              <div className="m">{relTime(t.updatedAt)}</div>
            </button>
            <button className="del" onClick={() => onDelete(t.id)} aria-label="Delete conversation">
              <Trash2 size={15} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
