// TEMPORARY inspection script — opens the forecast screen and captures evidence.
import { chromium } from 'playwright';

const UI = process.env.SMOKE_UI_URL || 'http://localhost:5173';
const API = process.env.SMOKE_API_URL || 'http://localhost:8000';
const OUT = process.env.OUT_DIR || '.tmp-forecast';

const login = async () => {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'demo@demandiq.app', password: 'DemandIQ2026!' }),
  });
  if (!res.ok) throw new Error(`login HTTP ${res.status}`);
  const body = await res.json();
  return body.access_token;
};

const log = (m) => process.stdout.write(`${m}\n`);

const main = async () => {
  const token = await login();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  await context.addInitScript((t) => localStorage.setItem('demandiq_token', t), token);
  const page = await context.newPage();

  const api = [];
  page.on('response', (r) => {
    const u = new URL(r.url());
    if (u.pathname.includes('/api/v1/')) api.push(`${r.status()} ${u.pathname}`);
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message.slice(0, 200)}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|ResizeObserver|DevTools/i.test(m.text())) {
      errors.push(`console: ${m.text().slice(0, 200)}`);
    }
  });

  await page.goto(`${UI}/app/forecast`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  const consent = page.getByRole('dialog', { name: 'Cookie preferences' });
  if (await consent.isVisible().catch(() => false)) {
    await consent.getByRole('button', { name: 'Essentials only' }).click();
    await consent.waitFor({ state: 'hidden' }).catch(() => {});
    log('dismissed cookie banner');
  }

  // Give the data hooks time to resolve.
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}/forecast-01-overview.png`, fullPage: true });
  log(`--- API CALLS ---\n${api.join('\n')}`);

  const text = await page.locator('body').innerText();
  log(`--- BODY TEXT ---\n${text.slice(0, 6000)}`);

  // The why-panel is a forecast sub-section; scroll it into view and grab it.
  const why = page.getByText(/Why/i).first();
  if (await why.count()) {
    await why.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/forecast-02-why.png`, fullPage: false });
    log('captured why-panel viewport');
  } else {
    log('no element matching /Why/ found');
  }

  log(`--- ERRORS ---\n${errors.length ? errors.join('\n') : '(none)'}`);
  await browser.close();
};

main().catch((e) => {
  log(`FAILED: ${e.message}`);
  process.exitCode = 1;
});
