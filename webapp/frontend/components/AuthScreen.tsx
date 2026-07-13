'use client';
import type { ReactNode } from 'react';
import { AgentMark } from './AgentMark';

// Full-screen branded auth/transition screen so the sign-in and callback states
// match the console instead of showing bare text. The pulsing logo (CSS rings +
// framer-motion mark) reads as "working"; pass `action` for error recovery.
export function AuthScreen({ heading, sub, action }: {
  heading: string;
  sub: string;
  action?: ReactNode;
}) {
  return (
    <main className="ngb-app" style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
      <div style={{ textAlign: 'center', maxWidth: 420, padding: '0 20px' }}>
        <div className="ngb-logo" style={{ margin: '0 auto 24px' }}>
          <span className="ring" />
          <span className="ring" />
          <span className="core"><AgentMark size={26} /></span>
        </div>
        <p className="ngb-eyebrow-dot" style={{ justifyContent: 'center', marginBottom: 16 }}>
          <span className="d" /> Console · neat-graph-bedrock
        </p>
        <h1 style={{ fontFamily: 'var(--ngb-display)', fontSize: 28, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 10px' }}>
          {heading}
        </h1>
        <p style={{ color: 'var(--muted)', fontSize: 14, margin: 0 }}>{sub}</p>
        {action && <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center' }}>{action}</div>}
      </div>
    </main>
  );
}
