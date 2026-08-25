/**
 * Capture review views for a demo. PACKAGED (v1.5.1): generalised from capture-girl-character.mjs so a
 * new character's demo id, render profile and scene-root name are all flags rather than hardcoded.
 *
 * PORTABLE ON PURPOSE. The existing capture-awp-v2-review.cjs hardcodes another machine's
 * /Users/tamlh/... playwright and Chromium paths, so it cannot run anywhere but that laptop. This
 * resolves the browser from the environment instead: CloakBrowser first (the configured default for
 * browser automation here), then a locally installed playwright. If neither is present it says so and
 * exits non-zero rather than producing zero screenshots and a green log.
 *
 * Usage:  node capture-character.mjs --demo <id> [--base URL] [--out DIR] [--profile FILE] [--root-name NAME]
 *
 * --profile defaults to work/render-profile.<demo>.json (the convention this project's own demos use).
 * --root-name defaults to <demo>-procedural (the convention createGirlCharacterModel.ts's root.name
 * follows) and is only used for an optional on-screen-bbox diagnostic -- getting it wrong skips that
 * diagnostic rather than failing the capture.
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};

const BASE = arg('base', 'http://127.0.0.1:5173/img2threejs-showcase/');
const DEMO = arg('demo', 'girl-character');
const ROOT_NAME = arg('root-name', `${DEMO}-procedural`);
/**
 * The background the render profile declares, measured off the reference (#0f0f0f). Capture mode
 * defaults to white because most demos' references are shot on white; this subject's is not, and a
 * render captured on the wrong background makes every foreground number a measurement of the
 * mismatch. Passed through to the viewer as ?bg=.
 */
const BG = arg('bg', '0f0f0f');
/** Read straight from the render profile so the capture cannot drift from the declared contract. */
const PROFILE = JSON.parse(fs.readFileSync(arg('profile', `work/render-profile.${DEMO}.json`), 'utf8'));
const PROFILE_CAMERA = {
  target: PROFILE.camera.target,
  radius: Math.hypot(
    PROFILE.camera.position[0] - PROFILE.camera.target[0],
    PROFILE.camera.position[2] - PROFILE.camera.target[2],
  ),
  fov: PROFILE.camera.fovDegrees,
};
const OUT = path.resolve(arg('out', path.join('.img2threejs', 'renders', 'pass-blockout')));

/** The six review angles. A single front view is not evidence about a 3D model. */
const VIEWS = [
  { id: 'front', azimuth: 0 },
  { id: 'orbit-plus-35', azimuth: 35 },
  { id: 'profile-left', azimuth: 90 },
  { id: 'rear', azimuth: 180 },
  { id: 'profile-right', azimuth: 270 },
  { id: 'orbit-minus-35', azimuth: -35 },
];

async function resolveBrowser() {
  try {
    const { launch } = await import('cloakbrowser');
    return { kind: 'cloakbrowser', launch: () => launch({ headless: true }) };
  } catch { /* fall through to playwright */ }
  try {
    const { chromium } = await import('playwright');
    return { kind: 'playwright', launch: () => chromium.launch({ headless: true }) };
  } catch { /* neither available */ }
  return null;
}

async function main() {
  const resolved = await resolveBrowser();
  if (!resolved) {
    console.error(
      'FAILED: no browser driver found. Install one of:\n'
      + '  npm i -D playwright && npx playwright install chromium\n'
      + '  (or make cloakbrowser importable from this project)\n'
      + 'Refusing to report success with zero screenshots.',
    );
    process.exit(2);
  }
  console.log(`browser driver: ${resolved.kind}`);
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await resolved.launch();
  const context = await browser.newContext({
    viewport: { width: 931, height: 2000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));

  const mask = argv.includes('--mask');
  // NO `capture=1`. Capture mode runs frameForCapture, which re-derives the camera from the scene
  // bounding box and KEEPS re-applying it: a profile-pinned camera set here was reverted within 250 ms
  // and produced a completely empty frame (0 non-background pixels vs 25,551 auto-framed). Worse, the
  // camera it chooses sits at y 0.875 — below the waist-rig mass at y 0.973 — so only that mass's
  // underside ever rendered, which is the "missing mass" chased across four iterations. The profile
  // camera has to win, or no per-pass number is comparable between passes.
  // MERGE with any query already on BASE instead of appending a second `?`. Passing
  // `--base '...?glb=shaded'` used to produce `...?glb=shaded?bg=000000`, where the whole tail is the
  // VALUE of `glb`, so the page silently loaded the default asset. Two captures then compared the
  // same file with itself and reported a 0.0/255 difference, which reads as "the two baselines are
  // identical" -- a conclusion about the assets drawn from a bug in the URL.
  const url = new URL(BASE, 'http://127.0.0.1');
  url.searchParams.set('bg', BG);
  if (mask) url.searchParams.set('mask', '1');
  url.hash = `#/demo/${DEMO}`;
  await page.goto(url.toString(), { waitUntil: 'networkidle' });
  await page.waitForSelector('canvas', { timeout: 120_000 });
  // The viewer sets this only after the first real frame; waiting on it rather than a fixed sleep is
  // what stops a blank canvas being captured and scored.
    // 120 s, not 30. Subdividing to 1.98 M triangles takes about 30 s of main-thread work, and a
  // capture that times out silently reuses the previous plate -- which is a stale image scored as if
  // it were the new one.
  await page.waitForFunction(() => window.__IMG2THREEJS_READY__ === true, { timeout: 120_000 });
  // The head arrives through `prewarm`, AFTER __IMG2THREEJS_READY__ fires. Capturing before it
  // attaches produced a plate with no head at all, and scored hair IoU as 0.000 rather than as a bug.
  await page.waitForFunction(() => {
    // Either path counts as ready: the implicit-surface build arrives through `prewarm` AFTER
    // __IMG2THREEJS_READY__ fires, while `?sdf=0` lofts everything synchronously and never sets
    // sdfSurface at all. Waiting only for sdfSurface hung the loft capture until it timed out.
    let sdf = false;
    let regions = 0;
    let meshes = 0;
    window.__IMG2THREEJS_VIEWER__?.scene?.traverse((o) => {
      if (o.userData?.sdfSurface) sdf = true;
      if (o.isMesh) meshes += 1;
      if (o.isMesh && o.userData?.region) regions += 1;
    });
    // THREE paths, not two. The GLB baseline demo carries no `region` tags and no sdfSurface at all --
    // it is the loaded asset -- so waiting on either of those hung it until the capture timed out and
    // it scored as if the comparison had been run.
    return sdf || regions >= 30 || meshes >= 8;
  }, { timeout: 120_000 });
  await page.evaluate(() => {
    document.querySelector('.demo-panel')?.setAttribute('style', 'display:none');
    document.querySelector('.hint')?.setAttribute('style', 'display:none');
  });

  const written = [];
  for (const view of VIEWS) {
    const placed = await page.evaluate(({ azimuthDeg, PROFILE_CAMERA, rootName }) => {
      const viewer = window.__IMG2THREEJS_VIEWER__;
      const camera = viewer?.camera;
      const controls = viewer?.controls;
      if (!camera) return { ok: false, reason: 'no camera on __IMG2THREEJS_VIEWER__' };
      // Pinned from the render profile, never inherited from the viewer's current framing. The AWP demo
      // recorded what inheriting costs: every geometry change reframed the shot and moved IoU ~0.039,
      // an order of magnitude above the per-pass signal being measured.
      const target = { x: PROFILE_CAMERA.target[0], y: PROFILE_CAMERA.target[1], z: PROFILE_CAMERA.target[2] };
      const radius = PROFILE_CAMERA.radius;
      camera.fov = PROFILE_CAMERA.fov;
      camera.updateProjectionMatrix();
      if (controls) controls.enabled = false;
      const theta = (azimuthDeg * Math.PI) / 180;
      camera.position.set(
        target.x + Math.sin(theta) * radius,
        target.y,
        target.z + Math.cos(theta) * radius,
      );
      camera.lookAt(target.x, target.y, target.z);
      camera.updateMatrixWorld(true);
      // SET THE CONTROLS' OWN TARGET BEFORE UPDATING THEM. `update()` re-derives the camera from
      // `controls.target`, which nothing here had ever synchronised with the profile, so the line
      // below used to throw away the camera placed just above and pull it back to the viewer's
      // default framing -- 43 px of vertical offset, measured.
      //
      // Disabling the controls does not prevent it, and the offset is invisible in the beauty plate
      // on its own. It only shows up against the semantic-ID pass, which sets this target and was
      // therefore 43 px away from the very pixels its masks were being used to measure: a pure
      // vertical shift of 43 px lifts the two passes' figure agreement from 0.8205 to 0.9941.
      //
      // Every per-region COLOUR number taken through those masks was reading the wrong pixels --
      // including the "lower body is 2-4x too bright" gap. Per-region IoU is unaffected, because it
      // compares two semantic-ID passes and both carried this same target.
      if (controls) {
        controls.target.set(target.x, target.y, target.z);
        controls.update();
        camera.updateMatrixWorld(true);
      }
      // `viewer.render` DOES NOT EXIST — optional chaining on it silently did nothing, so every frame
      // captured before this fix was whatever the render loop happened to draw, not the frame for the
      // camera just placed. Drive the real renderer directly and assert it is there, because a probe
      // that quietly no-ops is worse than one that fails.
      if (typeof viewer?.renderer?.render !== 'function') {
        return { ok: false, reason: 'viewer.renderer.render is not a function; cannot force a frame' };
      }
      viewer.renderer.render(viewer.scene, camera);

      // Record what the camera ACTUALLY is, plus where the model lands on screen. Six views are only
      // comparable if they share one orbit: same radius, same target, same fov. Capture mode re-frames
      // the camera for its own side-on plate, so assuming the orbit held is exactly the way a framing
      // difference gets mistaken for a geometry difference.
      const THREE = viewer?.THREE ?? window.THREE;
      let screen = null;
      const model = viewer?.scene?.getObjectByName?.(rootName);
      if (model && THREE?.Box3) {
        const box = new THREE.Box3().setFromObject(model);
        const pts = [];
        for (const x of [box.min.x, box.max.x]) {
          for (const y of [box.min.y, box.max.y]) {
            for (const z of [box.min.z, box.max.z]) {
              const v = new THREE.Vector3(x, y, z).project(camera);
              pts.push(v);
            }
          }
        }
        const xs = pts.map((v) => v.x); const ys = pts.map((v) => v.y);
        screen = {
          ndcMinX: Math.min(...xs), ndcMaxX: Math.max(...xs),
          ndcMinY: Math.min(...ys), ndcMaxY: Math.max(...ys),
          widthFraction: (Math.max(...xs) - Math.min(...xs)) / 2,
          heightFraction: (Math.max(...ys) - Math.min(...ys)) / 2,
        };
      }
      return {
        ok: true,
        radius,
        camera: {
          position: [camera.position.x, camera.position.y, camera.position.z],
          target: [target.x, target.y, target.z],
          fov: camera.fov,
        },
        screen,
      };
    }, { azimuthDeg: view.azimuth, PROFILE_CAMERA, rootName: ROOT_NAME });

    if (!placed.ok) {
      console.error(`FAILED placing camera for ${view.id}: ${placed.reason}`);
      await browser.close();
      process.exit(1);
    }
    await page.waitForTimeout(350);
    const file = path.join(OUT, `${view.id}.png`);
    await page.screenshot({ path: file, timeout: 120_000 });
    const bytes = fs.statSync(file).size;
    // A screenshot that exists but is a few hundred bytes is a blank frame, which would otherwise be
    // scored as if it were the model.
    if (bytes < 5_000) {
      console.error(`FAILED: ${view.id}.png is only ${bytes} bytes — that is a blank frame, not a render`);
      await browser.close();
      process.exit(1);
    }
    written.push({ view: view.id, azimuth: view.azimuth, file, bytes, camera: placed.camera, screen: placed.screen });
    const h = placed.screen ? placed.screen.heightFraction.toFixed(3) : 'n/a';
    const w = placed.screen ? placed.screen.widthFraction.toFixed(3) : 'n/a';
    console.log(
      `  ${view.id.padEnd(16)} az=${String(view.azimuth).padStart(4)}  ${(bytes / 1024).toFixed(0).padStart(3)} KB`
      + `  r=${placed.radius.toFixed(3)}  fov=${placed.camera.fov}  screenH=${h} screenW=${w}`,
    );
  }

  await browser.close();

  const manifest = {
    schemaVersion: 1,
    demo: DEMO,
    pass: 'blockout',
    passId: mask ? 'alpha-silhouette' : 'beauty',
    viewport: [931, 2000],
    captureFrozen: true,
    background: mask ? 'transparent (alpha)' : `#${BG}`,
    backgroundMatchesProfile: mask ? 'n/a — alpha capture is background-independent' : `#${BG}`,
    views: written,
    browserConsoleErrors: errors,
  };
  fs.writeFileSync(path.join(OUT, 'capture-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  console.log(`\n${written.length}/${VIEWS.length} views written to ${OUT}`);
  if (errors.length) {
    console.error(`\n${errors.length} browser console error(s):`);
    errors.slice(0, 5).forEach((e) => console.error('  ', e));
    process.exit(1);
  }
  console.log('no browser console errors');
}

main().catch((error) => { console.error('FAILED:', error); process.exit(1); });
