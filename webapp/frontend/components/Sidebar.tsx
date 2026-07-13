'use client';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Plus, Trash2, MessageSquare } from 'lucide-react';
import type { Thread } from '@/lib/types';

export function Sidebar({ threads, activeId, onSelect, onNew, onDelete }: {
  threads: Thread[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  const reduce = useReducedMotion();
  return (
    <aside className="flex w-64 flex-col border-r border-slate-200 dark:border-slate-700">
      <button onClick={onNew} className="m-3 flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm text-white">
        <Plus size={16} /> New chat
      </button>
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence>
          {threads.map((t) => (
            <motion.div
              key={t.id}
              initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className={`group flex items-center justify-between px-3 py-2 text-sm ${
                t.id === activeId ? 'bg-slate-100 dark:bg-slate-800' : ''
              }`}
            >
              <button onClick={() => onSelect(t.id)} className="flex items-center gap-2 truncate text-left">
                <MessageSquare size={14} /> <span className="truncate">{t.title}</span>
              </button>
              <button onClick={() => onDelete(t.id)} aria-label="Delete" className="opacity-0 group-hover:opacity-100">
                <Trash2 size={14} />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </aside>
  );
}
