import { defineConfig } from 'vitest/config'

// Separate from vite.config.ts on purpose: the tests here drive api.ts and
// previewCache.ts directly, so they want none of the dev server's proxy and
// none of the React plugin. api.ts reaches for window.setTimeout and
// previewCache.ts pulls in three.js, so they do want a DOM.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
