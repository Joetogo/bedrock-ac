import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  // Match the app's `@/*` -> project-root alias so runtime (non-type) imports
  // like `@/lib/chartSpec` resolve in tests, not just in `next build`.
  resolve: { alias: { '@': fileURLToPath(new URL('.', import.meta.url)) } },
  test: { environment: 'jsdom', globals: true },
});
