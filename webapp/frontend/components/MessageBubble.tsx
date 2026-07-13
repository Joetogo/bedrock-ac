'use client';
import { motion, useReducedMotion } from 'framer-motion';
import type { Message } from '@/lib/types';
import { MessageContent } from './MessageContent';

export function MessageBubble({ message }: { message: Message }) {
  const reduce = useReducedMotion();
  const isUser = message.role === 'user';
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div
        className={`max-w-[80ch] rounded-2xl border px-4 py-2.5 text-sm ${isUser ? 'whitespace-pre-wrap' : ''}`}
        style={
          isUser
            ? { background: 'var(--accent-dim)', borderColor: 'rgba(91,157,255,0.25)', color: 'var(--text)' }
            : { background: 'var(--card)', borderColor: 'var(--border)', color: 'var(--text)' }
        }
      >
        {isUser ? message.text : <MessageContent text={message.text} />}
      </div>
    </motion.div>
  );
}
