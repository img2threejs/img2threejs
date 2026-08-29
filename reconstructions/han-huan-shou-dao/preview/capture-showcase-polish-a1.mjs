import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import { statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.env.OUT_DIR || path.resolve(here, '..', 'captures', 'showcase-polish-a1');
const base = process.env.SHOWCASE_URL || 'http://127.0.0.1:4174';
const viewport = { width: 1440, height: 900 };
const target = [0.86, 0.02, 0];
// Must match registry.ts hero camera after polish iteration 2.
const heroPos = [1.52, 0.54, 4.62];

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome-stable',
  args: ['--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-webgl', '--ignore-gpu-blocklist'],
});

const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
const consoleErrors = [];
page.on('pageerror', (e) => consoleErrors.push(String(e)));
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text());
});

async function waitReady(timeout = 90000) {
  await page.waitForFunction(() => window.__IMG2THREEJS_READY__ === true, null, { timeout });
  await page.waitForTimeout(1200);
}

async function freezeIdle() {
  await page.evaluate(() => {
    const viewer = window.__IMG2THREEJS_VIEWER__;
    const root = viewer?.explodeRoot;
    if (root?.traverse) {
      root.traverse((o) => {
        if (o.userData?.tick) delete o.userData.tick;
      });
    }
    if (root?.userData) delete root.userData.tick;
  });
  await page.waitForTimeout(200);
}

async function setCamera(position, tgt) {
  await page.evaluate(({ position, tgt }) => {
    const viewer = window.__IMG2THREEJS_VIEWER__;
    if (!viewer) throw new Error('no viewer');
    viewer.camera.position.set(position[0], position[1], position[2]);
    viewer.controls.target.set(tgt[0], tgt[1], tgt[2]);
    viewer.controls.update();
    viewer.camera.updateProjectionMatrix();
    viewer.renderer.render(viewer.scene, viewer.camera);
  }, { position, tgt });
  await page.waitForTimeout(400);
}

async function shot(name, locator = 'canvas') {
  const dest = path.join(outDir, `${name}.png`);
  await page.locator(locator).first().screenshot({ path: dest, type: 'png' });
  console.log('wrote', dest, statSync(dest).size);
  return dest;
}

async function frameAndPixelAudit() {
  const frame = await page.evaluate(() => {
    const viewer = window.__IMG2THREEJS_VIEWER__;
    const root = viewer?.explodeRoot;
    const camera = viewer?.camera;
    if (!root || !camera) return { ready: false };

    root.updateWorldMatrix(true, true);
    const ndc = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    let meshCount = 0;
    root.traverse((node) => {
      if (!node.isMesh || !node.visible || !node.geometry) return;
      if (!node.geometry.boundingBox) node.geometry.computeBoundingBox();
      const box = node.geometry.boundingBox;
      if (!box) return;
      meshCount += 1;
      for (const x of [box.min.x, box.max.x]) {
        for (const y of [box.min.y, box.max.y]) {
          for (const z of [box.min.z, box.max.z]) {
            const point = box.min.clone().set(x, y, z).applyMatrix4(node.matrixWorld).project(camera);
            ndc.minX = Math.min(ndc.minX, point.x);
            ndc.maxX = Math.max(ndc.maxX, point.x);
            ndc.minY = Math.min(ndc.minY, point.y);
            ndc.maxY = Math.max(ndc.maxY, point.y);
          }
        }
      }
    });

    const margin = 0.985;
    return {
      ready: true,
      meshCount,
      ndc,
      fits: ndc.minX >= -margin && ndc.maxX <= margin && ndc.minY >= -margin && ndc.maxY <= margin,
      camera: {
        position: viewer.camera.position.toArray(),
        target: viewer.controls.target.toArray(),
        aspect: viewer.camera.aspect,
      },
    };
  });
  if (!frame.ready) return frame;

  // Read the browser-composited screenshot. WebGL's default back buffer is not
  // preserved, so drawImage(canvas) can be black even when the user sees a frame.
  const png = await page.locator('canvas').first().screenshot({ type: 'png' });
  const pixels = await page.evaluate(async (src) => {
    const image = new Image();
    image.src = src;
    await image.decode();
    const sample = document.createElement('canvas');
    sample.width = 64;
    sample.height = 64;
    const context = sample.getContext('2d', { willReadFrequently: true });
    context.drawImage(image, 0, 0, sample.width, sample.height);
    const data = context.getImageData(0, 0, sample.width, sample.height).data;
    let sum = 0;
    let sumSquares = 0;
    let minLuma = 255;
    let maxLuma = 0;
    const count = data.length / 4;
    for (let index = 0; index < data.length; index += 4) {
      const luma = data[index] * 0.2126 + data[index + 1] * 0.7152 + data[index + 2] * 0.0722;
      sum += luma;
      sumSquares += luma * luma;
      minLuma = Math.min(minLuma, luma);
      maxLuma = Math.max(maxLuma, luma);
    }
    const mean = sum / count;
    const variance = Math.max(0, sumSquares / count - mean * mean);
    return {
      minLuma: Number(minLuma.toFixed(2)),
      maxLuma: Number(maxLuma.toFixed(2)),
      variance: Number(variance.toFixed(2)),
      nonBlank: maxLuma - minLuma >= 8 && variance >= 4,
    };
  }, `data:image/png;base64,${png.toString('base64')}`);
  return { ...frame, pixels };
}

function orbitPos(azDeg, radius = 5.05, elev = 0.14) {
  const az = (azDeg * Math.PI) / 180;
  return [
    target[0] + Math.sin(az) * Math.cos(elev) * radius,
    target[1] + Math.sin(elev) * radius + 0.32,
    target[2] + Math.cos(az) * Math.cos(elev) * radius,
  ];
}

await page.goto(`${base}/#/demo/han-huan-shou-dao`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await waitReady();
const runtime = await page.evaluate(() => ({
  ready: window.__IMG2THREEJS_READY__ === true,
  runtime: window.__IMG2THREEJS_RUNTIME__,
  parts: {
    partCount: window.__IMG2THREEJS_PARTS__?.parts?.length ?? 0,
    integralMeshes: window.__IMG2THREEJS_PARTS__?.integralMeshes ?? null,
    unnamedMeshes: window.__IMG2THREEJS_PARTS__?.unnamedMeshes ?? null,
  },
  hasViewer: !!window.__IMG2THREEJS_VIEWER__,
  h2: document.querySelector('h2')?.textContent || null,
  canvas: (() => {
    const c = document.querySelector('canvas');
    return c ? { w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight } : null;
  })(),
}));
console.log('runtime', JSON.stringify(runtime, null, 2));
await freezeIdle();
await setCamera(heroPos, target);
const desktopAudit = await frameAndPixelAudit();
await shot('showcase-hero');
await page.screenshot({ path: path.join(outDir, 'showcase-hero-page.png'), type: 'png', fullPage: false });
console.log('wrote', path.join(outDir, 'showcase-hero-page.png'), statSync(path.join(outDir, 'showcase-hero-page.png')).size);

await setCamera(orbitPos(35), target);
await shot('showcase-orbit-plus35');
await setCamera(orbitPos(-35), target);
await shot('showcase-orbit-minus35');
await setCamera(orbitPos(90, 4.85, 0.06), target);
await shot('showcase-profile');

// Ring / handle closeup: pull toward pommel/handle.
await setCamera([2.18, 0.18, 1.55], [2.05, 0.02, 0]);
await shot('showcase-ring-handle-closeup');

const stats = await page.evaluate(() => {
  const viewer = window.__IMG2THREEJS_VIEWER__;
  const parts = window.__IMG2THREEJS_PARTS__;
  let meshCount = 0;
  let tri = 0;
  viewer?.scene?.traverse((o) => {
    if (o.isMesh && o.geometry) {
      meshCount += 1;
      const g = o.geometry;
      tri += Math.floor((g.index?.count || g.attributes?.position?.count || 0) / 3);
    }
  });
  return {
    meshCount,
    tri,
    partCount: parts?.parts?.length ?? 0,
    bg: viewer?.scene?.background?.getHexString?.() || null,
  };
});
console.log('stats', stats);

// White capture plate (evaluation framing via frameForCapture + captureMargin)
await page.goto(`${base}/#/demo/han-huan-shou-dao?capture=1`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await waitReady();
await page.waitForTimeout(800);
await shot('showcase-capture-white');

// Mobile viewport smoke
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${base}/#/demo/han-huan-shou-dao`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await waitReady();
await freezeIdle();
// Keep the viewer's responsive fit. Reapplying the desktop camera here defeats fitToViewport().
await page.waitForTimeout(500);
const mobileAudit = await frameAndPixelAudit();
await shot('showcase-mobile-smoke');
await page.screenshot({ path: path.join(outDir, 'showcase-mobile-page.png'), type: 'png', fullPage: false });
console.log('wrote', path.join(outDir, 'showcase-mobile-page.png'), statSync(path.join(outDir, 'showcase-mobile-page.png')).size);

await writeFile(path.join(outDir, 'capture-log.json'), JSON.stringify({
  runtime,
  stats,
  consoleErrors,
  url: base,
  demo: 'han-huan-shou-dao',
  cameras: { heroPos, target },
  audits: { desktop: desktopAudit, mobile: mobileAudit },
  at: new Date().toISOString(),
}, null, 2));

if (consoleErrors.length) console.error('console errors:', consoleErrors.slice(0, 30));
if (!stats.meshCount || stats.tri < 1000) {
  console.error('FAIL: model missing or too few triangles', stats);
  process.exit(2);
}
if (!desktopAudit.fits || !desktopAudit.pixels?.nonBlank || !mobileAudit.fits || !mobileAudit.pixels?.nonBlank) {
  console.error('FAIL: canvas framing or pixel audit', { desktopAudit, mobileAudit });
  process.exit(3);
}
console.log('OK');
await browser.close();
