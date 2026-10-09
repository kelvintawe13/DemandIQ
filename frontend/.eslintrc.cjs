/* DemandIQ frontend lint config.
 *
 * `npm run lint` (and `make lint-frontend`) shell out to ESLint. Until now the
 * repo shipped the script and the eslint/@typescript-eslint devDependencies but
 * no configuration file, so every invocation died with "couldn't find a
 * configuration file" — the gate was documentation-only. This config uses only
 * plugins already declared in package.json.
 *
 * Scope: correctness rules that catch real defects (hooks order, unused
 * bindings, unreachable code), not style — Prettier owns formatting.
 */
module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  extends: ['eslint:recommended', 'plugin:@typescript-eslint/recommended'],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  ignorePatterns: [
    'dist/',
    'coverage/',
    'node_modules/',
    'playwright-report/',
    'test-results/',
    '*.cjs',
  ],
  rules: {
    // React: a conditional hook is a crash, not a style nit.
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
    // Vite fast-refresh only works when a module exports components.
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    // `_`-prefixed bindings are deliberate discards.
    '@typescript-eslint/no-unused-vars': [
      'warn',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      },
    ],
    // The API client parses untyped JSON at the boundary on purpose; the
    // typed wrappers in src/api/** narrow it afterwards.
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-non-null-assertion': 'off',
    '@typescript-eslint/ban-ts-comment': 'off',
    // Empty catch blocks are used deliberately to keep a failed best-effort
    // read from stranding the user (see SigningIn / offline fallbacks).
    'no-empty': ['error', { allowEmptyCatch: true }],
  },
  overrides: [
    {
      // Test doubles are intentionally no-ops: the jsdom shims for
      // IntersectionObserver / ResizeObserver / matchMedia and the inert
      // event handlers in the primitive tests exist only to be *called*.
      files: ['src/__tests__/**/*.{ts,tsx}'],
      rules: {
        '@typescript-eslint/no-empty-function': 'off',
        'no-empty-pattern': 'off',
      },
    },
    {
      // Provider + `useX` hook in a single module is this app's context
      // convention (11 sites). Satisfying the rule means splitting every
      // context into two files; the only cost of leaving it is Fast Refresh
      // falling back to a full reload for those modules in development — a
      // dev-server ergonomic, not a correctness issue. Scoped to contexts so
      // the rule still guards components and pages, where a stray export is a
      // real signal.
      files: ['src/contexts/**/*.tsx'],
      rules: { 'react-refresh/only-export-components': 'off' },
    },
  ],
};
