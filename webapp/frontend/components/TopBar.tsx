'use client';
import { motion, useReducedMotion } from 'framer-motion';
import { LogOut } from 'lucide-react';

// Top chrome: brand (monogram + eyebrow) on the left; a live "ALL SYSTEMS"
// status pill and Sign out on the right. The status pill springs in on mount.
export function TopBar({ onSignOut }: { onSignOut: () => void }) {
  const reduce = useReducedMotion();
  return (
    <header className="ngb-topbar">
      <div className="ngb-brand">
        <span className="ngb-monogram">N.</span>
        <span className="rule" />
        <span className="ngb-eyebrow-dot"><span className="d" /> Console · neat-graph-bedrock</span>
      </div>
      <div className="ngb-top-actions">
        <motion.span
          className="ngb-pill ngb-status-pill"
          title="All upstreams reachable via the gateway"
          initial={reduce ? false : { opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ type: 'spring', stiffness: 380, damping: 24 }}
        >
          <span className="ngb-meter"><i /><i /><i /><i /></span>
          <span className="label">ALL SYSTEMS</span>
        </motion.span>
        <button className="ngb-pill" onClick={onSignOut} aria-label="Sign out">
          <LogOut size={15} /> Sign out
        </button>
      </div>
    </header>
  );
}
