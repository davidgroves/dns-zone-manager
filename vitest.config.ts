import { resolve } from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['frontend/__tests__/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['frontend/**/*.ts'],
      exclude: ['frontend/__tests__/**', 'frontend/main.ts'],
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'frontend'),
    },
  },
});
