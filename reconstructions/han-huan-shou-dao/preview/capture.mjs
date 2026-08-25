import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, '..', 'captures');
const url = process.env.PREVIEW_URL || 'http://127.0.0.1:4173/index.html';
const prefix = process.env.CAPTURE_PREFIX ? `${process.env.CAPTURE_PREFIX}-` : '';
const viewport = { width: 1680, height: 360 };

const shots = [
  { id: 'hero', azimuthDegrees: 0, elevationDegrees: 0, margin: 1.055, role: 'reference-match' },
  { id: 'orbit-plus35', azimuthDegrees: 35, elevationDegrees: 10, margin: 1.08, role: 'orbit' },
  { id: 'orbit-minus35', azimuthDegrees: -28, elevationDegrees: 10, margin: 1.08, role: 'orbit' },
  { id: 'profile', azimuthDegrees: 90, elevationDegrees: 0, margin: 1.18, role: 'true-side' },
  { id: 'rear', azimuthDegrees: 180, elevationDegrees: 8, margin: 1.08, role: 'orbit' },
  { id: 'head-hero', azimuthDegrees: 12, elevationDegrees: 10, margin: 1.2, role: 'head-closeup' },
  { id: 'head-threequarter', azimuthDegrees: 38, elevationDegrees: 16, margin: 1.25, role: 'head-closeup' },
  { id: 'topdown', azimuthDegrees: 0, elevationDegrees: 82, margin: 1.08, role: 'orbit' },
];

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
});
const page = await browser.newPage({
  viewport,
  deviceScaleFactor: 1,
});
const consoleErrors = [];
page.on('pageerror', (error) => consoleErrors.push(String(error)));
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text());
});

await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => Boolean(window.__IMG2THREEJS_READY__), null, { timeout: 60000 });
await page.evaluate(() => {
  const hud = document.getElementById('hud');
  if (hud) hud.style.display = 'none';
});

const results = [];
for (const shot of shots) {
  const set = await page.evaluate(async (camera) => {
    const api = window.__IMG2THREEJS_CAPTURE__;
    if (!api || typeof api.setCamera !== 'function') {
      return { ok: false, reason: 'setCamera missing' };
    }
    await api.setCamera(camera);
    return { ok: true };
  }, shot);
  if (!set.ok) throw new Error(set.reason || `setCamera failed for ${shot.id}`);
  await page.evaluate(async () => {
    for (let i = 0; i < 4; i += 1) {
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
  });
  const canvas = page.locator('canvas').first();
  const dest = path.join(outDir, `${prefix}${shot.id}.png`);
  await canvas.screenshot({ path: dest, type: 'png' });
  results.push({ id: shot.id, path: dest, ok: true });
}

const material = await page.evaluate(async () => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.getMaterialAudit !== 'function' || typeof api.setLightingMode !== 'function') {
    return { ok: false, reason: 'material capture API missing' };
  }
  const audit = api.getMaterialAudit();
  if (audit.failures.length) return { ok: false, reason: audit.failures.join('; '), audit };
  return { ok: true, audit };
});
if (!material.ok) throw new Error(material.reason || 'material audit failed');
await writeFile(path.join(outDir, `${prefix}material-audit.json`), JSON.stringify(material.audit, null, 2));

const lighting = await page.evaluate(() => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.getLightingAudit !== 'function') {
    return { ok: false, reason: 'lighting audit API missing' };
  }
  const audit = api.getLightingAudit();
  if (audit.failures.length) return { ok: false, reason: audit.failures.join('; '), audit };
  return { ok: true, audit };
});
if (!lighting.ok) throw new Error(lighting.reason || 'lighting audit failed');
await writeFile(path.join(outDir, `${prefix}lighting-audit.json`), JSON.stringify(lighting.audit, null, 2));

const interaction = await page.evaluate(() => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.getInteractionAudit !== 'function') {
    return { ok: false, reason: 'interaction audit API missing' };
  }
  const audit = api.getInteractionAudit();
  if (audit.failures.length) return { ok: false, reason: audit.failures.join('; '), audit };
  return { ok: true, audit };
});
if (!interaction.ok) throw new Error(interaction.reason || 'interaction audit failed');
await writeFile(path.join(outDir, `${prefix}interaction-audit.json`), JSON.stringify(interaction.audit, null, 2));

const performance = await page.evaluate(async () => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.getPerformanceAudit !== 'function') {
    return { ok: false, reason: 'performance audit API missing' };
  }
  const audit = await api.getPerformanceAudit();
  if (audit.failures.length) return { ok: false, reason: audit.failures.join('; '), audit };
  return { ok: true, audit };
});
await writeFile(path.join(outDir, `${prefix}performance-audit.json`), JSON.stringify(performance.audit, null, 2));
if (!performance.ok) throw new Error(performance.reason || 'performance audit failed');

const materialShots = [
  { id: 'material-neutral', lighting: 'neutral', camera: { azimuthDegrees: 0, elevationDegrees: 0, margin: 1.055 } },
  { id: 'material-reference', lighting: 'reference', camera: { azimuthDegrees: 0, elevationDegrees: 0, margin: 1.055 } },
  { id: 'material-grazing-blade', lighting: 'grazing', camera: { componentId: 'blade', azimuthDegrees: 18, elevationDegrees: 8, margin: 1.08 } },
  { id: 'material-grazing-handle', lighting: 'grazing', camera: { componentId: 'handle', azimuthDegrees: 32, elevationDegrees: 14, margin: 1.18 } },
];
for (const shot of materialShots) {
  await page.evaluate(async ({ lighting, camera }) => {
    const api = window.__IMG2THREEJS_CAPTURE__;
    api.setLightingMode(lighting);
    await api.setCamera(camera);
  }, shot);
  await page.evaluate(async () => {
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => requestAnimationFrame(resolve));
  });
  const dest = path.join(outDir, `${prefix}${shot.id}.png`);
  await page.locator('canvas').first().screenshot({ path: dest, type: 'png' });
  results.push({ id: shot.id, path: dest, ok: true, lighting: shot.lighting });
}
await page.evaluate(() => window.__IMG2THREEJS_CAPTURE__.setLightingMode('neutral'));

const assembly = await page.evaluate(async (camera) => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.getAssemblyManifest !== 'function' || typeof api.setExplode !== 'function') {
    return { ok: false, reason: 'assembly capture API missing' };
  }
  const manifest = api.getAssemblyManifest();
  const exploded = api.setExplode(1);
  await api.setCamera({ ...camera, margin: 1.08 });
  const selected = api.selectPart('handle');
  return { ok: true, manifest, exploded, selected };
}, shots[0]);
if (!assembly.ok) throw new Error(assembly.reason || 'assembly capture failed');
if (assembly.exploded.movedModules < 6 || assembly.exploded.maxDisplacement <= 0) {
  throw new Error('explode verification did not move all dao modules');
}
if (assembly.selected.moduleId !== 'handle') throw new Error('part selection did not resolve the handle module');
const explodedDest = path.join(outDir, `${prefix}assembly-exploded.png`);
await page.locator('canvas').first().screenshot({ path: explodedDest, type: 'png' });
results.push({ id: 'assembly-exploded', path: explodedDest, ok: true });
await writeFile(path.join(outDir, `${prefix}parts.json`), JSON.stringify(assembly.manifest, null, 2));
await writeFile(
  path.join(outDir, `${prefix}assembly-check.json`),
  JSON.stringify({ exploded: assembly.exploded, selected: assembly.selected }, null, 2),
);

await page.evaluate(async (camera) => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  api.selectPart(null);
  api.setExplode(0);
  await api.setCamera(camera);
}, shots[0]);

const selectionShots = [
  { id: 'selection-handle-integral', componentId: 'stud-c', moduleId: 'handle', camera: { componentId: 'handle', azimuthDegrees: 28, elevationDegrees: 12, margin: 1.22 } },
  { id: 'selection-ring-integral', componentId: 'ring-engraving-inner', moduleId: 'ring', camera: { componentId: 'ring', azimuthDegrees: 24, elevationDegrees: 12, margin: 1.22 } },
];
for (const shot of selectionShots) {
  const selected = await page.evaluate(async ({ componentId, camera }) => {
    const api = window.__IMG2THREEJS_CAPTURE__;
    const result = api.selectPart(componentId);
    await api.setCamera(camera);
    return result;
  }, shot);
  if (selected.moduleId !== shot.moduleId) {
    throw new Error(`${shot.componentId} resolved to ${selected.moduleId}, expected ${shot.moduleId}`);
  }
  const dest = path.join(outDir, `${prefix}${shot.id}.png`);
  await page.locator('canvas').first().screenshot({ path: dest, type: 'png' });
  results.push({ id: shot.id, path: dest, ok: true, selected });
}
await page.evaluate(async (camera) => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  api.selectPart(null);
  await api.setCamera(camera);
}, shots[0]);

const stripped = await page.evaluate(async (camera) => {
  const api = window.__IMG2THREEJS_CAPTURE__;
  if (!api || typeof api.setMapStripped !== 'function') {
    return { ok: false, reason: 'setMapStripped missing' };
  }
  api.setMapStripped(true);
  await api.setCamera(camera);
  return { ok: true };
}, shots[0]);
if (!stripped.ok) throw new Error(stripped.reason || 'map-stripped capture failed');
const strippedDest = path.join(outDir, `${prefix}map-stripped-hero.png`);
await page.locator('canvas').first().screenshot({ path: strippedDest, type: 'png' });
results.push({ id: 'map-stripped-hero', path: strippedDest, ok: true });

await writeFile(
  path.join(outDir, `${prefix}capture-log.json`),
  JSON.stringify({ url, viewport, consoleErrors, assembly: assembly.exploded, materialAudit: material.audit, lightingAudit: lighting.audit, interactionAudit: interaction.audit, performanceAudit: performance.audit, results }, null, 2),
);
await browser.close();

if (consoleErrors.length) {
  console.error(JSON.stringify({ consoleErrors, results }, null, 2));
  process.exit(2);
}
console.log(JSON.stringify({ captured: results.length, outDir, results }, null, 2));
