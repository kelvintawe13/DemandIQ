import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Short commit hash for the footer stamp (`src/lib/build.ts`).
 *
 * Order matters. `GIT_SHA` is passed in by `make docker-build` / `docker compose`
 * (a container build has no `.git` to ask, and the root lockfile is the only
 * thing copied in). `RAILWAY_GIT_COMMIT_SHA` is set by Railway itself on every
 * deploy, so the deployed footer names the commit without anyone configuring a
 * variable. Only with neither does this fall back to git, then to a timestamp —
 * still enough to tell two builds apart, which is the point: the footer has to
 * disagree with the build you just shipped before you blame the code.
 */
function buildId(): string {
  const provided = (process.env.GIT_SHA || process.env.RAILWAY_GIT_COMMIT_SHA || '').trim()
  if (provided) return provided.slice(0, 7)
  try {
    const sha = execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
    const dirty = execSync('git status --porcelain', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
    return dirty ? `${sha}-dirty` : sha
  } catch {
    return `t${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12)}`
  }
}

export default defineConfig(({ command }) => ({
  define: {
    __BUILD_ID__: JSON.stringify(buildId()),
  },
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
