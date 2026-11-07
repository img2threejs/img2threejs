import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const shots = path.join(here, 'verification');
await fs.mkdir(shots, { recursive: true });

const failures = [];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('console', message => {
  if (message.type() === 'error') errors.push(message.text());
});
page.on('pageerror', error => errors.push(String(error)));
page.on('requestfailed', request => errors.push(`request failed: ${request.url()}`));

await page.goto('http://127.0.0.1:8765/work/hive/viewer/', {
  waitUntil: 'networkidle',
  timeout: 60_000,
});
await page.waitForFunction(() => window.__hiveWorld?.assetsReady, null, { timeout: 20_000 });

const initial = await page.evaluate(() => ({
  activeRoom: window.__hiveWorld.activeRoom,
  roomButtons: document.querySelectorAll('[data-room]').length,
  roomPanels: document.querySelectorAll('[data-panel]').length,
  portals: window.__hiveWorld.portals.length,
  resources: performance.getEntriesByType('resource').length,
  navEntries: performance.getEntriesByType('navigation').length,
  overflowPx: document.documentElement.scrollWidth - innerWidth,
  renderer: {
    width: window.__hiveWorld.renderer.domElement.width,
    height: window.__hiveWorld.renderer.domElement.height,
  },
}));
await page.screenshot({ path: path.join(shots, 'exterior-desktop.png') });

if (initial.activeRoom !== 'exterior') failures.push(`initial room is ${initial.activeRoom}`);
if (initial.roomButtons !== 6 || initial.roomPanels !== 6 || initial.portals !== 5) {
  failures.push(`topology is ${initial.roomButtons} buttons / ${initial.roomPanels} panels / ${initial.portals} portals`);
}
if (initial.overflowPx !== 0) failures.push(`desktop overflow: ${initial.overflowPx}px`);

// Click a portal in the actual WebGL canvas, not its menu equivalent.
const portalPoint = await page.evaluate(() => {
  const portal = window.__hiveWorld.portals.find(item => item.userData.room === 'p1');
  const point = portal.getWorldPosition(new window.__hiveWorld.camera.position.constructor());
  point.project(window.__hiveWorld.camera);
  return { x: (point.x + 1) * innerWidth / 2, y: (-point.y + 1) * innerHeight / 2 };
});
await page.mouse.click(portalPoint.x, portalPoint.y);
await page.waitForFunction(() => window.__hiveWorld.activeRoom === 'p1');
await page.waitForTimeout(1400);
const portalResult = await page.evaluate(() => ({
  activeRoom: window.__hiveWorld.activeRoom,
  panel: document.querySelector('[data-panel="p1"]').classList.contains('active'),
  url: location.hash,
}));
if (portalResult.activeRoom !== 'p1' || !portalResult.panel || portalResult.url !== '#p1') {
  failures.push(`3D portal did not enter p1: ${JSON.stringify(portalResult)}`);
}

// One flight must have a real midpoint between origin and destination.
const startCamera = await page.evaluate(() => window.__hiveWorld.camera.position.toArray());
await page.locator('[data-room="kernel"]').click();
await page.waitForTimeout(260);
const midCamera = await page.evaluate(() => window.__hiveWorld.camera.position.toArray());
await page.waitForTimeout(1150);
const endCamera = await page.evaluate(() => window.__hiveWorld.camera.position.toArray());
const distance = (a, b) => Math.hypot(...a.map((value, index) => value - b[index]));
if (distance(startCamera, midCamera) < 0.1 || distance(midCamera, endCamera) < 0.1) {
  failures.push(`camera flight lacks a measurable midpoint: ${JSON.stringify({ startCamera, midCamera, endCamera })}`);
}
await page.screenshot({ path: path.join(shots, 'kernel-desktop.png') });

// Visit every room. No resource may be requested after initial preload.
for (const room of ['aguja', 'p1', 'p2', 'cripta', 'kernel', 'exterior']) {
  await page.locator(`[data-room="${room}"]`).click();
  await page.waitForFunction(expected => window.__hiveWorld.activeRoom === expected, room);
  await page.waitForTimeout(40);
}
const afterTravel = await page.evaluate(() => ({
  resources: performance.getEntriesByType('resource').length,
  navEntries: performance.getEntriesByType('navigation').length,
  panelsStillMounted: document.querySelectorAll('[data-panel]').length,
}));
if (afterTravel.resources !== initial.resources) {
  failures.push(`lazy resource activity detected: ${initial.resources} -> ${afterTravel.resources}`);
}
if (afterTravel.navEntries !== 1) failures.push(`page navigated ${afterTravel.navEntries} times`);
if (afterTravel.panelsStillMounted !== 6) failures.push(`only ${afterTravel.panelsStillMounted}/6 panels remain mounted`);

// Mobile keeps the map, rooms and canvas usable without horizontal overflow.
await page.setViewportSize({ width: 375, height: 812 });
await page.locator('.map-toggle').click();
await page.locator('[data-room="p2"]').click();
await page.waitForTimeout(80);
const mobile = await page.evaluate(() => ({
  activeRoom: window.__hiveWorld.activeRoom,
  overflowPx: document.documentElement.scrollWidth - innerWidth,
  panelVisible: document.querySelector('[data-panel="p2"]').classList.contains('active'),
  menuCollapsed: !document.querySelector('#castle-map').classList.contains('open'),
}));
await page.screenshot({ path: path.join(shots, 'p2-mobile.png') });
if (mobile.activeRoom !== 'p2' || !mobile.panelVisible || !mobile.menuCollapsed) {
  failures.push(`mobile navigation failed: ${JSON.stringify(mobile)}`);
}
if (mobile.overflowPx !== 0) failures.push(`mobile overflow: ${mobile.overflowPx}px`);

await browser.close();
const report = {
  verdict: failures.length ? 'FAIL' : 'PASS',
  initial,
  portalResult,
  cameraFlight: {
    start: startCamera,
    midpoint: midCamera,
    end: endCamera,
    firstLegDistance: Number(distance(startCamera, midCamera).toFixed(3)),
    secondLegDistance: Number(distance(midCamera, endCamera).toFixed(3)),
  },
  afterTravel,
  mobile,
  consoleErrors: errors,
  failures,
  screenshots: [
    path.join(shots, 'exterior-desktop.png'),
    path.join(shots, 'kernel-desktop.png'),
    path.join(shots, 'p2-mobile.png'),
  ],
};
if (errors.length) {
  report.verdict = 'FAIL';
  report.failures.push(`${errors.length} console/network error(s)`);
}
console.log(JSON.stringify(report, null, 2));
if (report.verdict !== 'PASS') process.exitCode = 1;
