import { config } from './config';
import { randomVerifier, challengeFromVerifier } from './pkce';

const VERIFIER_KEY = 'pkce_verifier';
const TOKEN_KEY = 'id_token';

export async function beginLogin(): Promise<string> {
  const verifier = randomVerifier();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const challenge = await challengeFromVerifier(verifier);
  const q = new URLSearchParams({
    client_id: config.clientId,
    response_type: 'code',
    scope: 'openid email profile',
    redirect_uri: config.redirectUri,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  });
  return `${config.cognitoDomain}/oauth2/authorize?${q.toString()}`;
}

export async function exchangeCode(code: string, fetchFn: typeof fetch = fetch): Promise<void> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY) ?? '';
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const resp = await fetchFn(`${config.cognitoDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!resp.ok) throw new Error('token exchange failed');
  const data = await resp.json();
  localStorage.setItem(TOKEN_KEY, data.id_token);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function isExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return typeof payload.exp !== 'number' || payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  const q = new URLSearchParams({
    client_id: config.clientId,
    logout_uri: config.redirectUri.replace('/callback', '/'),
  });
  window.location.href = `${config.cognitoDomain}/logout?${q.toString()}`;
}
