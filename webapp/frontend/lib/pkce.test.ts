import { describe, it, expect } from 'vitest';
import { randomVerifier, challengeFromVerifier, base64UrlEncode } from './pkce';

describe('pkce', () => {
  it('verifier is url-safe and long enough', () => {
    const v = randomVerifier();
    expect(v.length).toBeGreaterThanOrEqual(43);
    expect(v).toMatch(/^[A-Za-z0-9\-._~]+$/);
  });

  it('base64url has no +, / or =', () => {
    const s = base64UrlEncode(new Uint8Array([251, 252, 253]).buffer);
    expect(s).not.toMatch(/[+/=]/);
  });

  it('challenge is deterministic for a verifier', async () => {
    const c1 = await challengeFromVerifier('abc123');
    const c2 = await challengeFromVerifier('abc123');
    expect(c1).toBe(c2);
    expect(c1).not.toBe('abc123');
  });
});
