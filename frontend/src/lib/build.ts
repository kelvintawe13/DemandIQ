/**
 * Which build this bundle is.
 *
 * Injected by `vite.config.ts` as `__BUILD_ID__`: the short commit hash passed
 * in by the container build (`GIT_SHA`, or Railway's own
 * `RAILWAY_GIT_COMMIT_SHA`), else resolved from git, else a timestamp. It is
 * rendered in the page footer so "did my change actually reach this browser?"
 * is answerable by looking at the screen instead of guessing at caches: if the
 * footer does not name the build you just shipped, the tab is stale.
 *
 * Kept behind a `typeof` guard because the id is a compile-time define, and a
 * test or a plain `tsc` run has no such global.
 */
export const BUILD_ID: string = typeof __BUILD_ID__ === 'string' ? __BUILD_ID__ : 'dev';
