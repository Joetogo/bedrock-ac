import './globals.css';
import type { ReactNode } from 'react';
import { Inter, Inter_Tight, JetBrains_Mono } from 'next/font/google';

// Self-hosted at build (no CDN, CSP-safe on CloudFront). Exposed as CSS vars
// that globals.css maps onto --ngb-sans/--ngb-display/--ngb-mono.
const sans = Inter({ subsets: ['latin'], variable: '--font-sans', display: 'swap' });
const display = Inter_Tight({ subsets: ['latin'], variable: '--font-display', display: 'swap' });
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono', display: 'swap' });

export const metadata = { title: 'neat-graph-bedrock Console' };

export default function RootLayout({ children }: { children: ReactNode }) {
  // `dark` is forced (committed dark theme) so all `dark:` utilities activate.
  return (
    <html lang="en" className={`dark ${sans.variable} ${display.variable} ${mono.variable}`}>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
