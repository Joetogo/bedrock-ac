import { describe, it, expect, beforeEach, vi } from 'vitest';
import { beginLogin, exchangeCode, getToken, isExpired } from './auth';
import { config } from './config';

function makeJwt(expSecondsFromNow: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + expSecondsFromNow }));
  return `h.${payload}.s`;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('auth', () => {
  it('exchangeCode stores the id_token', async () => {
    sessionStorage.setItem('pkce_verifier', 'v');
    const fake = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id_token: 'TOKEN123' }),
    });
    await exchangeCode('the-code', fake as unknown as typeof fetch);
    expect(getToken()).toBe('TOKEN123');
    expect(fake).toHaveBeenCalledOnce();
  });

  it('isExpired true for a past token, false for a future one', () => {
    expect(isExpired(makeJwt(-10))).toBe(true);
    expect(isExpired(makeJwt(3600))).toBe(false);
  });

  it('beginLogin stores a verifier and builds a correct S256 authorize URL', async () => {
    const url = await beginLogin();

    const verifier = sessionStorage.getItem('pkce_verifier');
    expect(verifier).toEqual(expect.any(String));
    expect(verifier!.length).toBeGreaterThan(0);

    expect(url.startsWith(`${config.cognitoDomain}/oauth2/authorize?`)).toBe(true);

    const params = new URL(url, 'http://localhost').searchParams;
    expect(params.get('response_type')).toBe('code');
    expect(params.get('code_challenge_method')).toBe('S256');
    expect(params.get('code_challenge')).toEqual(expect.any(String));
    expect(params.get('code_challenge')!.length).toBeGreaterThan(0);
    expect(params.get('client_id')).toBe(config.clientId);
    expect(params.get('redirect_uri')).toBe(config.redirectUri);
    expect(params.get('scope')).toContain('openid');
  });

  it('exchangeCode sends a correct token request', async () => {
    sessionStorage.setItem('pkce_verifier', 'test-verifier');
    const fake = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id_token: 'TOK' }),
    });

    await exchangeCode('the-code', fake as unknown as typeof fetch);

    const [url, opts] = fake.mock.calls[0];
    expect(String(url).endsWith('/oauth2/token')).toBe(true);
    expect(opts.method).toBe('POST');
    expect(opts.headers['Content-Type']).toBe('application/x-www-form-urlencoded');

    const body = new URLSearchParams(opts.body.toString());
    expect(body.get('grant_type')).toBe('authorization_code');
    expect(body.get('code')).toBe('the-code');
    expect(body.get('code_verifier')).toBe('test-verifier');
    expect(body.has('client_id')).toBe(true);
    expect(body.has('redirect_uri')).toBe(true);
  });
});
