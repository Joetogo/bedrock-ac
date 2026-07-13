import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  // Committed dark theme — forced via `class="dark"` on <html>, so existing
  // `dark:` utilities (MessageContent tables/code, ChartBlock buttons) stay active.
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        ink: '#070a11',      // page ground
        accent: '#5b9dff',   // electric blue (was indigo) — flows to bg-accent/text-accent
        online: '#37d39a',
        warn: '#f4b23f',
      },
    },
  },
  plugins: [],
};
export default config;
