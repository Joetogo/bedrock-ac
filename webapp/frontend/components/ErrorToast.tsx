'use client';
import { useEffect, useRef } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { AlertCircle, X } from 'lucide-react';

export function ErrorToast({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  const reduce = useReducedMotion();

  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => onDismissRef.current(), 6000);
    return () => clearTimeout(timer);
  }, [message]);

  return (
    <AnimatePresence>
      {message && (
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }}
          className="fixed bottom-4 right-4 flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm text-white"
          role="alert"
        >
          <AlertCircle size={16} aria-hidden="true" />
          <span>{message}</span>
          <button onClick={onDismiss} aria-label="Dismiss" className="flex items-center justify-center">
            <X size={16} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
