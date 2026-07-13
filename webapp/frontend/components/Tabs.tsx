'use client';
import { Plus } from 'lucide-react';

export type TabKey = 'chat' | 'sessions' | 'mcps';

// Pill tab bar. Sessions shows a live count; a New chat action sits on the right.
export function Tabs({ active, onChange, sessionCount, connectionCount, onNewChat }: {
  active: TabKey;
  onChange: (t: TabKey) => void;
  sessionCount: number;
  connectionCount: number;
  onNewChat: () => void;
}) {
  const tab = (key: TabKey, label: string, badge?: number) => (
    <button
      className={`ngb-tab${active === key ? ' active' : ''}`}
      onClick={() => onChange(key)}
      role="tab"
      aria-selected={active === key}
    >
      {label}
      {badge !== undefined && badge > 0 && <span className="badge">{badge}</span>}
    </button>
  );
  return (
    <nav className="ngb-tabs" role="tablist">
      {tab('chat', 'Chat')}
      {tab('sessions', 'Sessions', sessionCount)}
      {tab('mcps', 'MCPs', connectionCount)}
      <button className="ngb-newchat" onClick={onNewChat}>
        <Plus size={15} /> New chat
      </button>
    </nav>
  );
}
