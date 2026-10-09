/**
 * DemandIQ route smoke test.
 *
 * Drives a real Chromium browser against a running stack and walks every
 * authenticated route, asserting that:
 *
 *   1. no `/api/v1/*` request answers with a non-2xx status,
 *   2. no uncaught page error (`pageerror`) is raised,
 *   3. no console error is logged (favicon / ResizeObserver / React DevTools
 *      notice are ignored — they are environment noise, not the product), and
 *   4. the page does not fall back to the app's error boundary copy.
 *
 * It relies on the same publicly-documented demo seats as
 * `backend/scripts/seed_demo_roles.py`, so run it against a dev stack that has
 * been seeded (`python backend/scripts/seed_demo_roles.py`). Override the
 * credentials with the `DEMANDIQ_*` env vars below to point it at another seat.
 *
 * Usage (servers must already be running — see `make e2e-smoke`):
 *   node e2e/route-smoke.mjs
 *   SMOKE_UI_URL=http://localhost:5173 SMOKE_API_URL=http://localhost:8000 node e2e/route-smoke.mjs
 *
 * The `playwright` browser library is a frontend devDependency; no test runner
 * is involved, which keeps this dependency-free and runnable anywhere the dev
 * servers are up.
 */

import { chromium } from 'playwright';

const UI = process.env.SMOKE_UI_URL || 'http://localhost:5173';
const API = process.env.SMOKE_API_URL || 'http://localhost:8000';

const ADMIN = {
  email: process.env.DEMANDIQ_ADMIN_EMAIL || 'admin@demandiq.app',
  password: process.env.DEMANDIQ_ADMIN_PASSWORD || 'Admin@2026!',
};
const OWNER = {
  email: process.env.DEMANDIQ_DEMO_EMAIL || 'demo@demandiq.app',
  password: process.env.DEMANDIQ_DEMO_PASSWORD || 'DemandIQ2026!',
};

// Routes reachable by an Administrator seat. `overview` intentionally redirects
// to `admin-models` (HOME_BY_ROLE in src/data/navItems.ts) — that redirect is
// expected, not a failure.
const ADMIN_ROUTES = [
  'overview',
  'analytics',
  'forecast',
  'ingestion',
  'sources',
  'quality',
  'alerts',
  'reports',
  'settings',
  'profile',
  'devices',
  'device-fleet',
  'billing',
  'admin-models',
  'admin-datasets',
  'admin-policies',
  'admin-connectors',
  'admin-accuracy',
  'admin-experiments',
  'admin-features',
  'admin-explainer',
  'admin-locations',
  'admin-locations-new',
  'admin-approvals',
  'admin-webhooks',
  'admin-pipeline',
  'admin-ai',
  'admin-access',
  'admin-site',
  'settings-preferences',
];

// Personal + Owner-only routes: reachable with the demo owner seat.
const OWNER_ROUTES = ['mirror-approvals', 'settings-preferences'];

// Public shell: no auth token, no data fetch expected.
const PUBLIC_ROUTES = ['', 'signin', 'signup'];

const CONSOLE_NOISE = /favicon|ResizeObserver|Download the React DevTools/i;
const ERROR_BOUNDARY =
  /Something went wrong|went wrong|Unable to load|Failed to load|could not be loaded/i;

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

async function login({ email, password }) {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(`login failed for ${email}: HTTP ${res.status}`);
  }
  const body = await res.json();
  if (!body.access_token) {
    throw new Error(`login for ${email} returned no access_token`);
  }
  return body.access_token;
}

async function probeRoute(browser, { route, token }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  if (token) {
    await context.addInitScript((t) => localStorage.setItem('demandiq_token', t), token);
  }
  // Dismiss the cookie-consent dialog and pin the theme to light so the visual
  // pass is deterministic regardless of stored preferences.
  await context.addInitScript(() => {
    const remove = () => document.querySelector('[role="dialog"]')?.remove();
    window.addEventListener('load', () => {
      setTimeout(remove, 400);
      setTimeout(remove, 1200);
    });
  });
  const page = await context.newPage();
  await page.route('**/api/v1/me/preferences', (r) => r.fulfill({ json: { theme: 'light' } }));

  const apiStatuses = [];
  page.on('response', (res) => {
    const url = res.url();
    if (!url.includes('/api/v1/')) return;
    apiStatuses.push({ status: res.status(), path: url.replace(API, '').split('?')[0] });
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message.slice(0, 160)}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !CONSOLE_NOISE.test(m.text())) {
      errors.push(`console: ${m.text().slice(0, 160)}`);
    }
  });

  const target = route ? `${UI}/${route}` : UI;
  const failures = [];
  try {
    await page.goto(target, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3200);

    const bad = [...new Set(apiStatuses.filter((a) => a.status >= 400).map((a) => `${a.status} ${a.path}`))];
    for (const b of bad) failures.push(`non-2xx API: ${b}`);

    const boundary = ERROR_BOUNDARY.test(await page.locator('body').innerText());
    if (boundary) failures.push('rendered the error boundary');

    for (const e of [...new Set(errors)]) failures.push(e);
  } catch (e) {
    failures.push(`navigation crashed: ${e.message}`);
  }

  const finalUrl = page.url().replace(UI, '') || '/';
  await context.close();
  return { route: route || '/', finalUrl, apiCount: apiStatuses.length, failures };
}

async function sweep(browser, { label, token, routes }) {
  log(`\n========== ${label} ==========`);
  const results = [];
  for (const route of routes) {
    results.push(await probeRoute(browser, { route, token }));
  }
  return results;
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch();
  } catch (e) {
    log(`✗ could not launch Chromium — run \`npx playwright install chromium\`. ${e.message}`);
    process.exit(1);
  }

  const all = [];
  try {
    const adminToken = await login(ADMIN);
    const ownerToken = await login(OWNER);

    all.push(
      ...(await sweep(browser, { label: `ADMIN — ${ADMIN.email}`, token: adminToken, routes: ADMIN_ROUTES })),
      ...(await sweep(browser, { label: `OWNER — ${OWNER.email}`, token: ownerToken, routes: OWNER_ROUTES })),
      ...(await sweep(browser, { label: 'PUBLIC (no auth)', token: null, routes: PUBLIC_ROUTES })),
    );
  } catch (e) {
    log(`\n✗ ${e.message}`);
    log('  Is the stack up and seeded? Start it with `make dev`, then seed seats with');
    log('  `python backend/scripts/seed_demo_roles.py`.');
    await browser.close();
    process.exit(1);
  }

  let failed = 0;
  for (const r of all) {
    const status = r.failures.length ? '✗' : '✓';
    if (r.failures.length) failed += 1;
    log(`${status} ${r.route.padEnd(28)} -> ${r.finalUrl}  (${r.apiCount} api calls)`);
    for (const f of r.failures) log(`      ${f}`);
  }

  await browser.close();

  const total = all.length;
  if (failed) {
    log(`\nFAILED: ${failed}/${total} route(s) had errors.`);
    process.exit(1);
  }
  log(`\nPASSED: ${total}/${total} route(s) clean — no non-2xx API call, no console error.`);
})();
