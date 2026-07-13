import { describe, it, expect, beforeEach, vi } from 'vitest';
import { sendChat, listConversations } from './api';

beforeEach(() => localStorage.setItem('id_token', 'TOK'));

const ok = (body: unknown, status = 200) => ({ ok: true, status, json: async () => body });

describe('api', () => {
  it('sendChat submits the prompt then polls the job until done', async () => {
    const fake = vi.fn()
      .mockResolvedValueOnce(ok({ jobId: 'j1', conversationId: 'c1' }, 202))
      .mockResolvedValueOnce(ok({ status: 'pending', conversationId: 'c1', answer: '', error: '' }))
      .mockResolvedValueOnce(ok({ status: 'done', conversationId: 'c1', answer: 'hi', error: '' }));

    const out = await sendChat('list rooms', null, fake as unknown as typeof fetch, 0);
    expect(out).toEqual({ answer: 'hi', conversationId: 'c1' });
    expect(fake).toHaveBeenCalledTimes(3);                 // submit + 2 polls

    const [submitUrl, submitOpts] = fake.mock.calls[0];
    expect(submitUrl).toContain('/chat');
    expect(submitOpts.method).toBe('POST');
    expect(submitOpts.headers.Authorization).toBe('Bearer TOK');
    expect(JSON.parse(submitOpts.body)).toEqual({ prompt: 'list rooms', conversationId: null });
    expect(fake.mock.calls[1][0]).toContain('/chat/j1');   // polls the returned job id
  });

  it('sendChat rides out a transient 5xx mid-poll and still resolves', async () => {
    const fake = vi.fn()
      .mockResolvedValueOnce(ok({ jobId: 'j1', conversationId: 'c1' }, 202))
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ error: 'unavailable' }) })
      .mockResolvedValueOnce(ok({ status: 'done', conversationId: 'c1', answer: 'hi', error: '' }));

    const out = await sendChat('q', null, fake as unknown as typeof fetch, 0);
    expect(out).toEqual({ answer: 'hi', conversationId: 'c1' });
    expect(fake).toHaveBeenCalledTimes(3);                 // submit + 503 (tolerated) + done
  });

  it('sendChat gives up after too many consecutive transient errors', async () => {
    const fake = vi.fn().mockImplementation(async (url: string) =>
      url.includes('/chat/')
        ? { ok: false, status: 503, json: async () => ({ error: 'unavailable' }) }
        : ok({ jobId: 'j1', conversationId: 'c1' }, 202),
    );
    await expect(sendChat('q', null, fake as unknown as typeof fetch, 0)).rejects.toThrow('(503)');
  });

  it('sendChat throws when the job reports an error', async () => {
    const fake = vi.fn()
      .mockResolvedValueOnce(ok({ jobId: 'j1', conversationId: 'c1' }, 202))
      .mockResolvedValueOnce(ok({ status: 'error', conversationId: 'c1', answer: '', error: 'boom' }));
    await expect(sendChat('q', null, fake as unknown as typeof fetch, 0)).rejects.toThrow('boom');
  });

  it('throws on non-2xx', async () => {
    const fake = vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => ({ error: 'x' }) });
    await expect(listConversations(fake as unknown as typeof fetch)).rejects.toThrow();
  });
});
