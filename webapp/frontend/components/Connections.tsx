'use client';

type Conn = {
  glyph: string;
  name: string;
  sub: string;
  status: 'up' | 'warnc';
  statusLabel: string;
  desc: string;
  tools: string[];
};

// Curated from the gateway's registered targets (read-only). This mirrors the
// deployed configuration; it is not a live health probe.
export const CONNECTIONS: Conn[] = [
  {
    glyph: 'NP', name: 'Neat Pulse', sub: 'meeting-room sensors',
    status: 'up', statusLabel: 'CONNECTED',
    desc: 'Room inventory plus CO₂, temperature, humidity, and occupancy for every Neat device.',
    tools: ['list_rooms', 'room_sensors', 'room_history'],
  },
  {
    glyph: 'TE', name: 'ThousandEyes', sub: 'network & voice tests',
    status: 'up', statusLabel: 'CONNECTED',
    desc: 'Test inventory, network path metrics, and RTP voice-quality results (v7, OAuth bearer).',
    tools: ['list_tests', 'network_results', 'rtp_results'],
  },
  {
    glyph: 'MG', name: 'MS Graph — Teams', sub: 'call records',
    status: 'warnc', statusLabel: '30-DAY WINDOW',
    desc: 'Teams callRecords with per-stream jitter, round-trip, and packet loss. Graph retains ~30 days.',
    tools: ['call_records', 'call_detail'],
  },
  {
    glyph: '▦', name: 'Correlate', sub: 'cross-source join',
    status: 'up', statusLabel: 'CONNECTED',
    desc: 'Joins room conditions against call/network quality over a shared time window (observational).',
    tools: ['correlate'],
  },
];

export function Connections() {
  return (
    <div className="ngb-conns">
      {CONNECTIONS.map((c) => (
        <div className={`ngb-conn ngb-stripe ${c.status === 'up' ? 'green' : 'amber'}`} key={c.name}>
          <div className="top">
            <span className="glyph">{c.glyph}</span>
            <div><div className="nm">{c.name}</div><div className="sub">{c.sub}</div></div>
            <span className={`st ${c.status}`}><span className="d" />{c.statusLabel}</span>
          </div>
          <div className="desc">{c.desc}</div>
          <div className="tools">{c.tools.map((t) => <span key={t}>{t}</span>)}</div>
        </div>
      ))}
      <div className="ngb-conns-note">
        <span>ⓘ</span>
        <span>
          Curated from the gateway&apos;s registered targets. All upstreams are <strong>read-only</strong>;
          credentials are read server-side from Secrets Manager and never reach the browser.
        </span>
      </div>
    </div>
  );
}
