'use client';
import { useEffect, useState, useCallback } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { getToken, isExpired, beginLogin, logout } from '@/lib/auth';
import { sendChat, listConversations, getConversation, deleteConversation } from '@/lib/api';
import type { Message, Thread } from '@/lib/types';
import { TopBar } from '@/components/TopBar';
import { AgentCard } from '@/components/AgentCard';
import { AgentMark } from '@/components/AgentMark';
import { Tabs, type TabKey } from '@/components/Tabs';
import { ChatThread } from '@/components/ChatThread';
import { Composer } from '@/components/Composer';
import { SessionList } from '@/components/SessionList';
import { Connections, CONNECTIONS } from '@/components/Connections';
import { ErrorToast } from '@/components/ErrorToast';
import { AuthScreen } from '@/components/AuthScreen';

const MODEL = 'claude-opus-4-8';
const REGION = 'us-east-1';

// Dev-only visual preview. Lets `next dev` render the redesigned UI without the
// Cognito round-trip (whose callback is pinned to the CloudFront origin, so it
// can never complete from localhost). The `NODE_ENV === 'development'` guard is
// statically false in `next build`, so this whole branch is dead-code-eliminated
// from any production bundle — it cannot weaken the deployed app.
const DEV_PREVIEW =
  process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_DEV_PREVIEW === '1';

const PREVIEW_ANSWER =
  '**11 calls** were placed across your Neat rooms in the last 7 days — call quality is ' +
  'excellent: zero packet loss, jitter at or below 17 ms.\n\n' +
  '```vega-lite\n' +
  '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","title":"Avg Round-Trip per Call (Last 7 Days)",' +
  '"width":"container","data":{"values":[{"call":"Jul-06 22:28","rtt":34},{"call":"Jul-07 01:06","rtt":46},' +
  '{"call":"Jul-07 22:01","rtt":33},{"call":"Jul-07 23:40","rtt":45},{"call":"Jul-08 00:59","rtt":45},' +
  '{"call":"Jul-08 01:22","rtt":32}]},"mark":{"type":"bar","tooltip":true},"encoding":{"x":{"field":"call",' +
  '"type":"nominal","title":"Call","sort":null},"y":{"field":"rtt","type":"quantitative","title":"ms"}}}\n' +
  '```\n\n' +
  '_Preview data — not from a live upstream._';

const PREVIEW_THREADS = (): Thread[] => {
  const now = Date.now();
  return [
    { id: 'p1', title: 'graph of call stats for my neat rooms', updatedAt: new Date(now - 36e5).toISOString() },
    { id: 'p2', title: 'where are my thousandeyes tests?', updatedAt: new Date(now - 864e5).toISOString() },
    { id: 'p3', title: 'list my neat rooms', updatedAt: new Date(now - 1728e5).toISOString() },
  ];
};

export default function Home() {
  const reduce = useReducedMotion();
  const [ready, setReady] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>('chat');

  useEffect(() => {
    if (DEV_PREVIEW) {
      setReady(true);
      setThreads(PREVIEW_THREADS());
      return;
    }
    const tok = getToken();
    if (!tok || isExpired(tok)) {
      beginLogin()
        .then((u) => (window.location.href = u))
        .catch(() => setAuthError('Could not start sign-in. Please retry.'));
      return;
    }
    setReady(true);
    listConversations().then(setThreads).catch(() => setError('could not load history'));
  }, []);

  const refreshThreads = useCallback(() => {
    listConversations().then(setThreads).catch(() => {});
  }, []);

  const openThread = async (id: string) => {
    setActiveId(id);
    setMessages([]);
    setTab('chat');
    if (DEV_PREVIEW) {
      setMessages([
        { role: 'user', text: 'show me a trend of call quality across my Neat rooms', ts: new Date().toISOString() },
        { role: 'assistant', text: PREVIEW_ANSWER, ts: new Date().toISOString() },
      ]);
      return;
    }
    try { setMessages(await getConversation(id)); }
    catch { setError('could not open conversation'); }
  };

  const newChat = () => { setActiveId(null); setMessages([]); setTab('chat'); };

  // A 401 means the (non-refreshable) Cognito id token has expired. Login only
  // requests openid/email/profile — no offline_access — so there is no refresh
  // token; the sole recovery is to bounce through the hosted UI for a fresh one.
  const reauth = () => {
    beginLogin()
      .then((u) => (window.location.href = u))
      .catch(() => setAuthError('Could not start sign-in. Please retry.'));
  };

  const onSend = async (text: string) => {
    setTab('chat');
    if (DEV_PREVIEW) {
      setMessages((m) => [...m, { role: 'user', text, ts: new Date().toISOString() }]);
      setPending(true);
      // Simulate the agent turn so the boxed thinking indicator + chart render.
      setTimeout(() => {
        setMessages((m) => [...m, { role: 'assistant', text: PREVIEW_ANSWER, ts: new Date().toISOString() }]);
        setPending(false);
      }, 1600);
      return;
    }
    const tok = getToken();
    if (!tok || isExpired(tok)) {
      setError('Session expired — signing you back in…');
      reauth();
      return;
    }
    const ts = new Date().toISOString();
    setMessages((m) => [...m, { role: 'user', text, ts }]);
    setPending(true);
    try {
      const res = await sendChat(text, activeId);
      setActiveId(res.conversationId);
      setMessages((m) => [...m, { role: 'assistant', text: res.answer, ts: new Date().toISOString() }]);
      refreshThreads();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'the agent could not be reached';
      // A mid-flight 401 (token expired during the poll) gets the same recovery.
      if (msg.includes('(401)')) {
        setError('Session expired — signing you back in…');
        reauth();
      } else {
        // Surface the real cause (503, timeout, job error) so it's diagnosable.
        setError(msg);
      }
    } finally {
      setPending(false);
    }
  };

  const onDelete = async (id: string) => {
    if (DEV_PREVIEW) {
      setThreads((t) => t.filter((x) => x.id !== id));
      if (id === activeId) newChat();
      return;
    }
    try {
      await deleteConversation(id);
      if (id === activeId) newChat();
      refreshThreads();
    } catch { setError('could not delete conversation'); }
  };

  if (!ready) {
    return (
      <AuthScreen
        heading={authError ? 'Sign-in needed' : 'Signing you in…'}
        sub={authError ?? 'Redirecting you to secure sign-in…'}
        action={authError ? <button className="ngb-pill" onClick={reauth}>Try again</button> : undefined}
      />
    );
  }

  const head =
    tab === 'chat'
      ? { name: 'neat-graph-bedrock', hint: 'READ-ONLY · MEMORY ON', mark: true }
      : tab === 'sessions'
        ? { name: 'Sessions', hint: 'PER-USER · PRIVATE', mark: false }
        : { name: 'Connections', hint: 'VIA AGENTCORE GATEWAY', mark: false };

  return (
    <main className="ngb-app">
      <TopBar onSignOut={DEV_PREVIEW ? () => undefined : logout} />

      <section className="ngb-hero">
        <p className="eyebrow"><span className="d" /> Console · rooms · calls · tests</p>
        <h1>Console</h1>
        <p className="subtitle">
          Ask in plain language across your Neat rooms, Teams calls, and ThousandEyes tests —
          answered with charts, correlations, and read-only precision.
        </p>
        <p className="metaline">{REGION} · sandbox</p>
      </section>

      <AgentCard model={MODEL} region={REGION} />

      <Tabs
        active={tab}
        onChange={setTab}
        sessionCount={threads.length}
        connectionCount={CONNECTIONS.length}
        onNewChat={newChat}
      />

      <div className="ngb-panel">
        <div className="ngb-panel-head">
          {head.mark && <span className="mini"><AgentMark size={13} animate={false} /></span>}
          <span className="name">{head.name}</span>
          <span className="hint">{head.hint}</span>
        </div>
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, y: -6 }}
            transition={{ duration: 0.18 }}
          >
            {tab === 'chat' && <ChatThread messages={messages} pending={pending} />}
            {tab === 'sessions' && (
              <SessionList threads={threads} activeId={activeId} onSelect={openThread} onDelete={onDelete} />
            )}
            {tab === 'mcps' && <Connections />}
          </motion.div>
        </AnimatePresence>
      </div>

      <Composer disabled={pending} onSend={onSend} />

      <ErrorToast message={error} onDismiss={() => setError(null)} />
    </main>
  );
}
