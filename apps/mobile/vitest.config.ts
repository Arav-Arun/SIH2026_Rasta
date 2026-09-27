import { defineConfig } from 'vitest/config';

/** Pure logic only. */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['services/**/*.test.ts'],
  },
});
