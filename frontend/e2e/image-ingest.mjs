/**
 * DemandIQ image-ingestion E2E.
 *
 * Drives a real Chromium browser through the product UI:
 *
 *   1. signs in over the real API and restores that session in localStorage,
 *   2. opens Data Ingestion → Image (Handwritten),
 *   3. uploads a generated photo of a two-row log,
 *   4. waits for the backend OCR job to populate the preview,
 *   5. asserts the photo thumbnail, both extracted rows, and that a row can be
 *      edited inline,
 *   6. opens the confirmation dialog and checks it reflects the edit.
 *
 * The final import is deliberately NOT confirmed: this test must not write
 * demand observations. The staged upload it creates is deleted through the API
 * on the way out, whether the test passed or failed.
 *
 * The image is drawn in the browser rather than checked in as a PNG. That keeps
 * the fixture reviewable (the dates and counts are right there in this file) and
 * avoids an opaque binary that only one person knows how to regenerate.
 *
 * Prerequisites:
 *   - the stack is running (see `make docker-up` and DOCKER.md),
 *   - the admin seat exists (`python backend/scripts/seed_site_admin.py`),
 *   - the backend image includes OCR: `BACKEND_EXTRAS=ocr` (DOCKER.md),
 *   - the frontend Playwright dependency is installed and Chromium is available:
 *       npx playwright install chromium
 *
 * Usage:
 *   node e2e/image-ingest.mjs
 *   SMOKE_UI_URL=http://localhost:5173 SMOKE_API_URL=http://localhost:8000 node e2e/image-ingest.mjs
 *   SMOKE_CHROME_PATH="/path/to/Google Chrome" node e2e/image-ingest.mjs
 *
 * The production container serves the SPA under /app, while Vite serves it at
 * the root. If SMOKE_APP_PATH is not set, both are tried, so the same command
 * works against either topology. Set `SMOKE_APP_PATH=` to force the root.
 */

import assert from 'node:assert/strict';
import { chromium } from 'playwright';

/** @typedef {import('playwright').Browser} Browser */
/** @typedef {import('playwright').Page} Page */

const UI = process.env.SMOKE_UI_URL || 'http://localhost:5173';
const API = process.env.SMOKE_API_URL || 'http://localhost:8000';

const ADMIN = {
  email: process.env.DEMANDIQ_ADMIN_EMAIL || 'admin@demandiq.app',
  password: process.env.DEMANDIQ_ADMIN_PASSWORD || 'Admin@2026!',
};

// Rows are chosen so each line contains exactly one candidate count after the
// date. That makes the assertion about extraction, not about Tesseract's
// handling of ambiguous slash-like digits (which the preview flags instead).
const ROWS = [
  { date: '2026-01-12', demand: 112, line: '12/01/2026   112' },
  { date: '2026-01-13', demand: 58, line: '13/01/2026   58' },
];
const IMAGE_NAME = 'handwritten-log.png';
const EDITED_DEMAND = 64;

const CONSOLE_NOISE = /favicon|ResizeObserver|Download the React DevTools/i;

/** @param {string} message */
function log(message) {
  process.stdout.write(`${message}\n`);
}

/** @returns {Promise<string>} */
async function login() {
  const response = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ADMIN),
  });
  if (!response.ok) {
    throw new Error(
      `login failed for ${ADMIN.email}: HTTP ${response.status}. ` +
        'Is the stack seeded? Run `python backend/scripts/seed_site_admin.py`.',
    );
  }
  const body = await response.json();
  if (!body.access_token) throw new Error(`login for ${ADMIN.email} returned no access_token`);
  return body.access_token;
}

/**
 * Open ingestion on whichever mount path this deployment uses.
 * Returns the prefix that worked, e.g. `/app` or an empty string.
 */
/**
 * @param {Page} page
 * @returns {Promise<string>}
 */
async function openIngestion(page) {
  const requested = process.env.SMOKE_APP_PATH;
  const candidates = requested !== undefined ? [requested] : ['/app', ''];
  const errors = [];

  for (const candidate of candidates) {
    const prefix = candidate.replace(/\/+$/, '');
    try {
      await page.goto(`${UI}${prefix}/ingestion`, { waitUntil: 'domcontentloaded' });
      await page
        .getByRole('tab', { name: /Image \(Handwritten\)/i })
        .waitFor({ state: 'visible', timeout: 10000 });
      return prefix;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      errors.push(`${prefix || '/'} -> ${message.split('\n')[0]}`);
    }
  }

  throw new Error(
    `data ingestion did not load at ${UI}. Tried:\n  ${errors.join('\n  ')}\n` +
      'Check SMOKE_APP_PATH, the frontend build, and the admin seat.',
  );
}

/**
 * Draw a clean two-row log in the page.
 *
 * @param {string[]} lines
 * @returns {string} PNG data URL
 */
function drawLog(lines) {
  const canvas = document.createElement('canvas');
  canvas.width = 1200;
  canvas.height = 560;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas 2D context unavailable');

  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#000000';
  context.font = '64px sans-serif';
  lines.forEach((line, index) => context.fillText(line, 48, 150 + index * 200));

  return canvas.toDataURL('image/png');
}

/**
 * @param {Page} page
 * @returns {Promise<Buffer>}
 */
async function makeLogImage(page) {
  const dataUrl = await page.evaluate(drawLog, ROWS.map((row) => row.line));

  if (!dataUrl.startsWith('data:image/png;base64,')) {
    throw new Error('canvas did not produce a PNG data URL');
  }
  return Buffer.from(dataUrl.slice('data:image/png;base64,'.length), 'base64');
}

/**
 * @param {string} jobId
 * @param {string} token
 * @returns {Promise<void>}
 */
async function discardUpload(jobId, token) {
  const response = await fetch(`${API}/api/v1/ingest/uploads/${jobId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
  // 200 carries the removal report (`{deleted, filename, removed_rows}`); 204 is
  // what older builds answered, and a 404 means it is already gone — the
  // desired end state either way.
  if (![200, 204, 404].includes(response.status)) {
    throw new Error(`could not discard staged upload ${jobId}: HTTP ${response.status}`);
  }
}

/** @returns {Promise<void>} */
async function main() {
  const token = await login();
  /** @type {Browser | undefined} */
  let browser;
  /** @type {string | undefined} */
  let jobId;
  /** Whether the UI's Cancel action already removed the staged job. */
  let cancelled = false;
  /** @type {Error | null} */
  let failure = null;

  try {
    browser = await chromium.launch(
      process.env.SMOKE_CHROME_PATH ? { executablePath: process.env.SMOKE_CHROME_PATH } : {},
    );
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.addInitScript((value) => {
      localStorage.setItem('demandiq_token', value);
    }, token);
    const page = await context.newPage();

    /** @type {string[]} */
    const apiErrors = [];
    page.on('response', (response) => {
      const url = new URL(response.url());
      if (url.pathname.includes('/api/v1/') && response.status() >= 400) {
        apiErrors.push(`${response.status()} ${url.pathname}`);
      }
    });
    page.on('pageerror', (error) => {
      apiErrors.push(`pageerror: ${error.message.slice(0, 200)}`);
    });
    page.on('console', (message) => {
      if (message.type() === 'error' && !CONSOLE_NOISE.test(message.text())) {
        apiErrors.push(`console: ${message.text().slice(0, 200)}`);
      }
    });

    const appPath = await openIngestion(page);
    log(`✓ opened ${UI}${appPath}/ingestion`);

    // The first-run cookie banner is fixed above the bottom-right corner and
    // would intercept the Review & Confirm click. Store a choice, as a user
    // must, instead of deleting the overlay behind the product's back.
    const consent = page.getByRole('dialog', { name: 'Cookie preferences' });
    await consent.waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
    if (await consent.isVisible()) {
      await consent.getByRole('button', { name: 'Essentials only' }).click();
      await consent.waitFor({ state: 'hidden' });
      log('✓ dismissed the cookie banner');
    }

    await page.getByRole('tab', { name: /Image \(Handwritten\)/i }).click();
    await page.getByRole('heading', { name: 'Upload Image' }).waitFor();

    const uploaded = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === '/api/v1/ingest/image',
      { timeout: 30000 },
    );
    const image = await makeLogImage(page);
    await page
      .getByLabel('Handwritten record photo')
      .setInputFiles({ name: IMAGE_NAME, mimeType: 'image/png', buffer: image });

    const uploadResponse = await uploaded;
    assert.equal(uploadResponse.status(), 201, 'POST /ingest/image should answer 201');
    jobId = (await uploadResponse.json()).job_id;
    assert.ok(jobId, 'POST /ingest/image should return a job id');
    log(`✓ uploaded ${IMAGE_NAME} (${image.length} bytes) as job ${jobId}`);

    // OCR runs synchronously in the request in the local engine, but the panel
    // fetches the ready job independently. Wait for the rendered rows instead
    // of assuming the preview is populated when the POST returns.
    await page.waitForFunction(
      (count) => document.querySelectorAll('input[aria-label^="Date for row"]').length === count,
      ROWS.length,
      { timeout: 60000 },
    );

    const previewText = await page.locator('body').innerText();
    assert.match(
      previewText,
      new RegExp(`${ROWS.length} rows detected`),
      'preview should report both rows',
    );
    assert.match(
      previewText,
      /Read by the local OCR engine/i,
      'the local Tesseract path should be used',
    );
    assert.doesNotMatch(previewText, /No rows detected/i, 'OCR should find rows in the generated log');

    const thumbnail = page.getByAltText(IMAGE_NAME);
    await thumbnail.waitFor({ state: 'visible' });
    const dimensions = await thumbnail.evaluate((element) => {
      const image = /** @type {HTMLImageElement} */ (element);
      return { width: image.naturalWidth, height: image.naturalHeight };
    });
    assert.ok(
      dimensions.width > 0 && dimensions.height > 0,
      'the uploaded image should be visible',
    );
    log(`✓ preview shows the photo (${dimensions.width}×${dimensions.height}) and ${ROWS.length} rows`);

    const dates = page.locator('input[aria-label^="Date for row"]');
    const visitors = page.locator('input[aria-label^="Visitors for row"]');
    assert.equal(await dates.count(), ROWS.length, 'preview should render one date input per row');
    assert.equal(await visitors.count(), ROWS.length, 'preview should render one visitor input per row');

    for (const [index, row] of ROWS.entries()) {
      assert.equal(
        await dates.nth(index).inputValue(),
        row.date,
        `row ${index + 1} should have the date from the photo`,
      );
      assert.equal(
        await visitors.nth(index).inputValue(),
        String(row.demand),
        `row ${index + 1} should have the visitor count from the photo`,
      );
    }

    // Prove the preview is editable, not just a rendered table. The confirmation
    // dialog has to show the corrected value, which is what the operator would
    // actually import.
    await visitors.nth(1).fill(String(EDITED_DEMAND));
    assert.equal(await visitors.nth(1).inputValue(), String(EDITED_DEMAND));

    const confirm = page.getByRole('button', { name: /Review & Confirm/i });
    assert.equal(
      await confirm.isEnabled(),
      true,
      'Review & Confirm should be enabled once rows exist',
    );
    await confirm.click();

    const dialog = page.getByRole('dialog');
    await dialog.waitFor({ state: 'visible' });
    const dialogText = await dialog.innerText();
    assert.match(dialogText, /Confirm extracted rows/i);
    assert.match(dialogText, new RegExp(`${ROWS.length} rows`, 'i'));
    assert.match(dialogText, new RegExp(ROWS[0].date));
    assert.match(
      dialogText,
      new RegExp(`${EDITED_DEMAND} covers`),
      'the edited value should reach the confirmation',
    );

    await dialog.getByRole('button', { name: /Keep editing/i }).click();
    await dialog.waitFor({ state: 'hidden' });
    log(`✓ extracted dates/counts are editable; confirmation reflects ${EDITED_DEMAND} covers`);

    // Cancel is the way out of a staged job. It has to clear both panels and
    // delete the job; deleting it behind the panel's back is what used to leave
    // the preview pointing at a 404 and the upload card showing a dead thumbnail.
    page.once('dialog', async (confirm) => confirm.accept());
    await page.getByRole('button', { name: /Cancel upload/i }).click();
    await page.getByText('No data extracted yet').waitFor({ timeout: 10000 });
    assert.equal(
      await page.getByAltText(IMAGE_NAME).count(),
      0,
      'cancelling should clear the discarded thumbnail',
    );
    assert.equal(
      await page.getByText('Extraction failed').count(),
      0,
      'cancelling should not leave the preview in an error state',
    );
    cancelled = true;
    log('✓ cancelled the staged job and reset both panels');

    assert.deepEqual(apiErrors, [], `browser/API errors:\n${apiErrors.join('\n')}`);
    log('\nPASSED: image upload, OCR preview, inline edit, confirmation and cancel all work.');
  } catch (error) {
    failure = error instanceof Error ? error : new Error(String(error));
  } finally {
    if (jobId && !cancelled) {
      try {
        await discardUpload(jobId, token);
        log(`✓ discarded staged upload ${jobId}`);
      } catch (error) {
        const cleanupError = error instanceof Error ? error : new Error(String(error));
        failure = failure ?? cleanupError;
        log(`✗ cleanup failed: ${cleanupError.message}`);
      }
    }
    if (browser) await browser.close();
  }

  if (failure) {
    log(`\nFAILED: ${failure.message}`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  log(`\nFAILED: ${error.message}`);
  process.exitCode = 1;
});
