import { config } from './config';
import { getToken } from './auth';
import type { Message, Thread, ChatResult, ChatSubmit, JobStatus } from './types';

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 90;   // ~3 min ceiling, matched to the Lambda worker timeout
const POLL_MAX_TRANSIENT = 3;   // consecutive 5xx/network blips tolerated mid-poll before giving up

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function req<T>(path: string, init: RequestInit, fetchFn: typeof fetch): Promise<T> {
  const resp = await fetchFn(`${config.apiBase}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken() ?? ''}`,
      ...(init.headers ?? {}),
    },
  });
  if (!resp.ok) {
    // Carry the status so callers can distinguish auth (401) from transient
    // backend blips (5xx) without re-parsing the message string.
    const err = new Error(`request failed (${resp.status})`) as Error & { status?: number };
    err.status = resp.status;
    throw err;
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// Submit the turn, then poll the job until the async worker finishes. Keeps the
// {answer, conversationId} contract so callers don't care that it's async now.
export async function sendChat(prompt: string, conversationId: string | null,
                               fetchFn: typeof fetch = fetch,
                               pollMs: number = POLL_INTERVAL_MS): Promise<ChatResult> {
  const submit = await req<ChatSubmit>('/chat', {
    method: 'POST', body: JSON.stringify({ prompt, conversationId }),
  }, fetchFn);

  let transient = 0;
  for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
    let job: JobStatus;
    try {
      job = await req<JobStatus>(`/chat/${submit.jobId}`, { method: 'GET' }, fetchFn);
    } catch (e) {
      // A transient 5xx (or a network error, which has no status) mid-poll can
      // kill an answer that is still computing. Ride out a few before failing;
      // a 4xx (e.g. 401) is real and rethrows immediately.
      const status = (e as { status?: number }).status;
      if ((status === undefined || status >= 500) && transient < POLL_MAX_TRANSIENT) {
        transient++;
        await sleep(pollMs);
        continue;
      }
      throw e;
    }
    transient = 0;
    if (job.status === 'done') return { answer: job.answer, conversationId: submit.conversationId };
    if (job.status === 'error') throw new Error(job.error || 'agent error');
    await sleep(pollMs);
  }
  throw new Error('timed out waiting for the agent');
}

export async function listConversations(fetchFn: typeof fetch = fetch): Promise<Thread[]> {
  const data = await req<{ conversations: Thread[] }>('/conversations', { method: 'GET' }, fetchFn);
  return data.conversations;
}

export async function getConversation(id: string, fetchFn: typeof fetch = fetch): Promise<Message[]> {
  const data = await req<{ messages: Message[] }>(`/conversations/${id}`, { method: 'GET' }, fetchFn);
  return data.messages;
}

export function deleteConversation(id: string, fetchFn: typeof fetch = fetch): Promise<void> {
  return req<void>(`/conversations/${id}`, { method: 'DELETE' }, fetchFn);
}
