import {mkdir, stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '@playwright/test';

const url = new URL(process.env.MVP_URL ?? 'http://127.0.0.1:4173');
url.searchParams.set('captureTime', '1.25');
const artifactDirectory = fileURLToPath(new URL('../artifacts/', import.meta.url));
const fixedCapture = fileURLToPath(new URL('../artifacts/refinery.png', import.meta.url));
const leftCapture = fileURLToPath(new URL('../artifacts/refinery-orbit-left.png', import.meta.url));
const rightCapture = fileURLToPath(new URL('../artifacts/refinery-orbit-right.png', import.meta.url));

await mkdir(artifactDirectory, {recursive: true});

const browser = await chromium.launch({headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 1024}, deviceScaleFactor: 1});
const pageErrors = [];
page.on('pageerror', (error) => pageErrors.push(error));

async function waitForPaint() {
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
}

async function orbit(deltaX) {
  const canvasBounds = await page.locator('canvas').boundingBox();
  if (!canvasBounds) {
    throw new Error('Canvas bounds are unavailable.');
  }
  const startX = canvasBounds.x + canvasBounds.width * 0.55;
  const startY = canvasBounds.y + canvasBounds.height * 0.55;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + deltaX, startY - 35, {steps: 14});
  await page.mouse.up();
  await waitForPaint();
}

try {
  await page.goto(url.href, {waitUntil: 'networkidle'});
  const canvas = page.locator('canvas');
  const reset = page.locator('[data-testid="reset-camera"]');
  await expect(canvas).toBeVisible();
  await expect(reset).toBeVisible();
  await expect(page.getByText('视觉近似，非工程模型')).toBeVisible();

  await reset.click();
  await waitForPaint();
  await page.screenshot({path: fixedCapture, fullPage: true});

  await orbit(-210);
  await page.screenshot({path: leftCapture, fullPage: true});

  await reset.click();
  await orbit(210);
  await page.screenshot({path: rightCapture, fullPage: true});

  if (pageErrors.length > 0) {
    throw new Error(`Page errors: ${pageErrors.map((error) => error.message).join(' | ')}`);
  }

  for (const capture of [fixedCapture, leftCapture, rightCapture]) {
    const captureStat = await stat(capture);
    if (captureStat.size === 0) {
      throw new Error(`Empty screenshot: ${capture}`);
    }
  }
  process.stdout.write('Smoke passed at deterministic animation time 1.25s: canvas, reset, label, zero page errors, and 3 non-empty captures.\n');
} finally {
  await browser.close();
}
