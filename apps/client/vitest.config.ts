import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      '@': root,
      // vinext supplies next/* at build time; unit tests use a plain anchor.
      'next/link': path.join(root, 'test/stubs/next-link.tsx'),
    },
  },
  test: {
    environment: 'node',
    environmentOptions: {
      jsdom: { url: 'http://localhost/' },
    },
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: [
      'lib/**/*.test.ts',
      'components/**/*.test.{ts,tsx}',
      'hooks/**/*.test.ts',
    ],
  },
});
