'use client';
import { useEffect, useState } from 'react';
import { exchangeCode } from '@/lib/auth';
import { AuthScreen } from '@/components/AuthScreen';

export default function Callback() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code');
    if (!code) { setError('missing authorization code'); return; }
    exchangeCode(code)
      .then(() => { window.location.href = '/'; })
      .catch(() => setError('login failed'));
  }, []);
  return (
    <AuthScreen
      heading={error ? 'Sign-in failed' : 'Signing you in…'}
      sub={error ? 'We couldn’t complete sign-in. Please try again.' : 'Finalising your secure session…'}
      action={error ? <a className="ngb-pill" href="/">Back to console</a> : undefined}
    />
  );
}
