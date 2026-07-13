'use client';
import { MessageBubble } from './MessageBubble';
import { ThinkingIndicator } from './ThinkingIndicator';
import type { Message } from '@/lib/types';

export function ChatThread({ messages, pending }: { messages: Message[]; pending: boolean }) {
  const empty = messages.length === 0 && !pending;
  return (
    <div className="ngb-thread">
      {empty && (
        <div className="m-auto max-w-sm text-center text-sm" style={{ color: 'var(--dim)' }}>
          Ask about your Neat rooms, Teams call quality, or ThousandEyes tests —
          answers come back with charts when the data calls for it.
        </div>
      )}
      {messages.map((m) => <MessageBubble key={m.ts + m.role} message={m} />)}
      {pending && <ThinkingIndicator />}
    </div>
  );
}
