'use client';
import { motion, useReducedMotion } from 'framer-motion';
import { AgentMark } from './AgentMark';

// The identity card: animated logo (rotating mark + CSS pulse rings), name,
// live ONLINE status, and MODEL/REGION spec pills. Springs up on mount.
export function AgentCard({ model, region }: { model: string; region: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.section
      className="ngb-agent-card ngb-stripe blue"
      initial={reduce ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 220, damping: 26 }}
    >
      <div className="ngb-logo">
        <span className="ring" />
        <span className="ring" />
        <span className="core"><AgentMark size={26} /></span>
      </div>
      <div className="ngb-agent-id">
        <span className="ngb-eyebrow-dot"><span className="d" /> Agent · neat-graph-bedrock</span>
        <h2>neat-graph-bedrock</h2>
        <p>Correlation agent — meeting-room conditions, call quality, and network health.</p>
        <span className="ngb-verdict"><span className="d" /> READY</span>
      </div>
      <div className="ngb-tiles">
        <div className="ngb-tile"><span className="v">{model}</span><span className="k">Model</span></div>
        <div className="ngb-tile"><span className="v">{region}</span><span className="k">Region</span></div>
        <div className="ngb-tile"><span className="v">ON</span><span className="k">Memory</span></div>
        <div className="ngb-tile"><span className="v">READ-ONLY</span><span className="k">Access</span></div>
      </div>
    </motion.section>
  );
}
