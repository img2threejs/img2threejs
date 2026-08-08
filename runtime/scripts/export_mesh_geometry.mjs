#!/usr/bin/env node
/**
 * Dump a running Three.js preview's mesh geometry to the `meshes.json` shape the geometry gates
 * consume.
 *
 * WHY THIS EXISTS: `SKILL.md` already instructs
 *
 *     node runtime/scripts/export_mesh_geometry.mjs --url <preview> --out meshes.json
 *     python3 forge/stage4_review/self_intersection.py meshes.json --json
 *
 * but the file did not exist, and neither did `runtime/`. The consequence was silent, not loud:
 * `self_intersection.py` and `geometry_integrity.py` have no other producer in this repo, so the
 * self-intersection and seam-overlap gates were simply never runnable, while the surrounding
 * checklist still read as complete. This closes that.
 *
 * OUTPUT (matches `geometry_integrity.measure_geometry_integrity` / `self_intersection.py`):
 *   { "meshes": [ { "id", "name", "vertices": [[x,y,z]...], "indices": [flat*3],
 *                   "normals": [[x,y,z]...], "triangleCount", "role"?, "attachment"? } ],
 *     "performanceBudget"?: { "triangleBudget": n } }
 *
 * Vertices are emitted in WORLD space. Local space would be enough for per-mesh
 * self-intersection, but `geometry_integrity`'s seam-overlap check compares ADJACENT MESH PAIRS,
 * which is only meaningful once every part shares one frame.
 *
 * InstancedMesh is expanded to one entry per instance (`name#0`, `name#1`, ...). Collapsing it to
 * a single entry would hide exactly the case those gates exist to catch: seven rivets that all
 * resolve to the same matrix, or one that drifts out of its counterbore.
 *
 * The browser driver is NOT hardcoded. Existing capture scripts in this workspace `require()` an
 * absolute path into one user's npx cache, which works on one machine and nowhere else; that is a
 * known fragility, not a pattern to copy. Resolution order is --driver, then
 * $IMG2THREEJS_PLAYWRIGHT, then a bare `playwright` import. If none resolve, this exits 2 with all
 * three named rather than emitting an empty file that would read as "no defects".
 */
import { writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname } from 'node:path';

function parseArgs(argv) {
  const out = {
    url: null, outPath: null, driver: process.env.IMG2THREEJS_PLAYWRIGHT || null,
    browser: process.env.IMG2THREEJS_CHROMIUM || null, waitMs: 1500,
    selector: 'canvas', triangleBudget: null, headed: false, includeInvisible: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--url') out.url = argv[++i];
    else if (a === '--out') out.outPath = argv[++i];
    else if (a === '--driver') out.driver = argv[++i];
    else if (a === '--browser') out.browser = argv[++i];
    else if (a === '--wait-ms') out.waitMs = Number(argv[++i]);
    else if (a === '--selector') out.selector = argv[++i];
    else if (a === '--triangle-budget') out.triangleBudget = Number(argv[++i]);
    else if (a === '--headed') out.headed = true;
    else if (a === '--include-invisible') out.includeInvisible = true;
    else if (a === '-h' || a === '--help') out.help = true;
    else throw new Error(`unknown flag: ${a}`);
  }
  return out;
}

const USAGE = `usage: export_mesh_geometry.mjs --url <preview-url> --out <meshes.json>
                                 [--driver <playwright module path>] [--browser <chromium exe>]
                                 [--wait-ms 1500] [--selector canvas] [--triangle-budget N]
                                 [--headed] [--include-invisible]

Dumps world-space mesh geometry from a running Three.js preview for
forge/stage4_review/self_intersection.py and geometry_integrity.py.

The page must expose a scene. Recognised handles, in order:
  window.__IMG2THREEJS_VIEWER__.scene   (the showcase Viewer contract)
  window.__IMG2THREEJS_SCENE__
  window.__THREE_SCENE__
`;

async function loadPlaywright(driver) {
  const attempts = [];
  // Try require() first: Playwright ships CommonJS, and a --driver pointing at an installed
  // package DIRECTORY (the usual way to reach a cached install) is resolvable by require but not
  // by ESM import(), which rejects directory specifiers outright.
  const require = createRequire(import.meta.url);
  for (const spec of [driver, 'playwright'].filter(Boolean)) {
    try {
      return require(spec);
    } catch (err) {
      attempts.push(`require(${spec}): ${err.message.split('\n')[0]}`);
    }
    try {
      return await import(spec);
    } catch (err) {
      attempts.push(`import(${spec}): ${err.message.split('\n')[0]}`);
    }
  }
  throw new Error(
    'could not load Playwright. Pass --driver <path to the playwright module>, set '
    + '$IMG2THREEJS_PLAYWRIGHT, or install it (npm i -D playwright). Tried:\n  '
    + attempts.join('\n  '),
  );
}

/** Runs INSIDE the page. Kept dependency-free and defensive: a preview that half-loaded should
 *  produce a named error, not a plausible-looking short mesh list. */
const EXTRACT = (includeInvisible) => {
  const w = window;
  const scene = w.__IMG2THREEJS_VIEWER__?.scene || w.__IMG2THREEJS_SCENE__ || w.__THREE_SCENE__;
  if (!scene) {
    return { error: 'no scene handle on window (looked for __IMG2THREEJS_VIEWER__.scene, __IMG2THREEJS_SCENE__, __THREE_SCENE__)' };
  }
  const meshes = [];
  const skipped = [];
  scene.updateMatrixWorld(true);

  const emit = (obj, geom, matrix, idSuffix) => {
    const pos = geom.getAttribute && geom.getAttribute('position');
    if (!pos) { skipped.push({ name: obj.name || '(unnamed)', reason: 'no position attribute' }); return; }
    const nrm = geom.getAttribute && geom.getAttribute('normal');
    const vertices = [];
    const normals = [];
    // Apply the world matrix by hand: reading obj.matrixWorld.elements avoids depending on a
    // THREE symbol being reachable from the page's module scope.
    const e = matrix.elements;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
      vertices.push([
        e[0] * x + e[4] * y + e[8] * z + e[12],
        e[1] * x + e[5] * y + e[9] * z + e[13],
        e[2] * x + e[6] * y + e[10] * z + e[14],
      ]);
      if (nrm) {
        // Normals ignore translation; this is the plain linear part, adequate for the
        // face-vs-vertex-normal consistency check (no non-uniform-scale correction).
        const nx = nrm.getX(i), ny = nrm.getY(i), nz = nrm.getZ(i);
        const lx = e[0] * nx + e[4] * ny + e[8] * nz;
        const ly = e[1] * nx + e[5] * ny + e[9] * nz;
        const lz = e[2] * nx + e[6] * ny + e[10] * nz;
        const len = Math.hypot(lx, ly, lz) || 1;
        normals.push([lx / len, ly / len, lz / len]);
      }
    }
    let indices;
    if (geom.index) {
      indices = Array.from(geom.index.array, (v) => Number(v));
    } else {
      // Non-indexed geometry is the common output of a lofted/warped build; synthesise the
      // implicit 0,1,2,... triangle list so the consumer sees the same topology.
      indices = Array.from({ length: pos.count }, (_, i) => i);
    }
    const entry = {
      id: (obj.name || 'mesh') + (idSuffix ?? ''),
      name: (obj.name || 'mesh') + (idSuffix ?? ''),
      vertices,
      indices,
      triangleCount: Math.floor(indices.length / 3),
    };
    if (normals.length === vertices.length) entry.normals = normals;
    const rt = obj.userData || {};
    if (rt.role) entry.role = rt.role;
    if (rt.explodeWithParent) entry.explodeWithParent = true;
    meshes.push(entry);
  };

  scene.traverse((obj) => {
    if (!obj.isMesh && !obj.isInstancedMesh) return;
    if (!includeInvisible && obj.visible === false) {
      skipped.push({ name: obj.name || '(unnamed)', reason: 'not visible' });
      return;
    }
    const geom = obj.geometry;
    if (!geom) { skipped.push({ name: obj.name || '(unnamed)', reason: 'no geometry' }); return; }
    if (obj.isInstancedMesh) {
      const m = new obj.matrixWorld.constructor();
      for (let i = 0; i < obj.count; i++) {
        const inst = new obj.matrixWorld.constructor();
        obj.getMatrixAt(i, inst);
        m.copy(obj.matrixWorld).multiply(inst);
        emit(obj, geom, m, `#${i}`);
      }
    } else {
      emit(obj, geom, obj.matrixWorld, '');
    }
  });
  return { meshes, skipped };
};

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err.message}\n\n${USAGE}`);
    process.exit(2);
  }
  if (args.help) { process.stdout.write(USAGE); return; }
  if (!args.url || !args.outPath) {
    process.stderr.write(`--url and --out are both required\n\n${USAGE}`);
    process.exit(2);
  }

  let chromium;
  try {
    ({ chromium } = await loadPlaywright(args.driver));
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    process.exit(2);
  }

  const launchOpts = { headless: !args.headed };
  if (args.browser) launchOpts.executablePath = args.browser;
  const browser = await chromium.launch(launchOpts);
  const consoleErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    page.on('pageerror', (e) => consoleErrors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    await page.goto(args.url, { waitUntil: 'networkidle' });
    if (args.selector) await page.waitForSelector(args.selector);
    // Prefer the page's own readiness flag when it publishes one; the fixed wait is the fallback.
    await page.waitForFunction(() => window.__IMG2THREEJS_READY__ === true, null, { timeout: 8000 })
      .catch(() => {});
    await page.waitForTimeout(args.waitMs);

    const result = await page.evaluate(EXTRACT, args.includeInvisible);
    if (result.error) {
      process.stderr.write(`export_mesh_geometry: ${result.error}\n`);
      process.exit(2);
    }
    if (!result.meshes.length) {
      // Fail closed. An empty meshes.json would make every downstream gate report "clean".
      process.stderr.write(
        'export_mesh_geometry: the scene exposed no meshes. Refusing to write an empty '
        + 'meshes.json, which every geometry gate would read as a clean pass.\n'
        + `skipped: ${JSON.stringify(result.skipped)}\n`,
      );
      process.exit(2);
    }

    const payload = { meshes: result.meshes, skipped: result.skipped, source: args.url };
    if (args.triangleBudget) payload.performanceBudget = { triangleBudget: args.triangleBudget };
    mkdirSync(dirname(args.outPath), { recursive: true });
    writeFileSync(args.outPath, `${JSON.stringify(payload, null, 1)}\n`);

    const tris = result.meshes.reduce((n, m) => n + m.triangleCount, 0);
    process.stdout.write(
      `${JSON.stringify({
        out: args.outPath, meshes: result.meshes.length, triangles: tris,
        skipped: result.skipped.length, consoleErrors: consoleErrors.length,
      })}\n`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((err) => { process.stderr.write(`${err.stack || err}\n`); process.exit(2); });
