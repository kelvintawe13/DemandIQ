import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // The dev server — and vitest — serve the app at the root, which is why the
  // README says http://localhost:5173. The built artifact is served by the API
  // under `/app` (`backend/app/main.py` mounts `frontend/dist` there), so its
  // asset URLs and the shell's own route paths both have to carry that prefix;
  // without it `/app/index.html` asked for `/assets/...` and every deep link
  // 404'd inside the mount.
  base: command === 'build' ? '/app/' : '/',
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  preview: {
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    globals: true,
    include: ['src/__tests__/**/*.test.{ts,tsx}'],
  },
}))
