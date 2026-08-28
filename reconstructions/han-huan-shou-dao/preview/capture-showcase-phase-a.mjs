import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.env.OUT_DIR || path.resolve(here, '..', 'captures', 'showcase-phase-a');
const base = process.env.SHOWCASE_URL || 'http://127.0.0.1:4174';
const viewport = { width: 1440, height: 900 };
const target = [1.15, 0.02, 0];
// Authored gallery camera (must match registry.ts)
const heroPos = [1.95, 0.72, 5.35];

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
  await page.waitForTimeout(1000);
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
  await page.waitForTimeout(350);
}

async function shot(name) {
  const dest = path.join(outDir, `${name}.png`);
  await page.locator('canvas').first().screenshot({ path: dest, type: 'png' });
  console.log('wrote', dest, (await import('node:fs')).statSync(dest).size);
  return dest;
}

function orbitPos(azDeg, radius = 5.6, elev = 0.14) {
  const az = (azDeg * Math.PI) / 180;
  return [
    target[0] + Math.sin(az) * Math.cos(elev) * radius,
    target[1] + Math.sin(elev) * radius + 0.35,
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
}));
console.log('runtime', JSON.stringify(runtime, null, 2));
await freezeIdle();
await setCamera(heroPos, target);
await shot('showcase-hero');
await page.screenshot({ path: path.join(outDir, 'showcase-hero-page.png'), type: 'png', fullPage: false });

await setCamera(orbitPos(35), target);
await shot('showcase-orbit-plus35');
await setCamera(orbitPos(-30), target);
await shot('showcase-orbit-minus35');
await setCamera(orbitPos(90, 5.2, 0.08), target);
await shot('showcase-profile');
await setCamera([3.2, 1.35, 4.4], target);
await shot('showcase-threequarter');

// White capture plate (evaluation framing)
await page.goto(`${base}/#/demo/han-huan-shou-dao?capture=1`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await waitReady();
await page.waitForTimeout(700);
await shot('showcase-capture-white');

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

await writeFile(path.join(outDir, 'capture-log.json'), JSON.stringify({
  runtime,
  stats,
  consoleErrors,
  url: base,
  demo: 'han-huan-shou-dao',
  cameras: { heroPos, target },
  at: new Date().toISOString(),
}, null, 2));

if (consoleErrors.length) console.error('console errors:', consoleErrors.slice(0, 30));
if (!stats.meshCount || stats.tri < 1000) {
  console.error('FAIL: model missing or too few triangles', stats);
  process.exit(2);
}
console.log('OK');
await browser.close();
