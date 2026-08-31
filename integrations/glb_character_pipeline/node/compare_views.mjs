/**
 * US-004: score the code-only build against the GLB baseline, on identical framing.
 *
 * The existing comparator is Python and needs numpy, which is not installed here. Rather than skip the
 * measurement -- the whole acceptance criterion is "the picture did not change" -- the arithmetic runs
 * in a browser, which can decode a PNG and hand back pixels without any dependency at all.
 *
 * Three figures per view, the same three the earlier runs recorded:
 *
 *   IoU            intersection over union of the two silhouettes, where a silhouette is every pixel
 *                  that is not the declared background
 *   noise          mean |Laplacian| of luma inside the silhouette, one figure per side. This is a
 *                  SURFACE-ROUGHNESS reading, not an error: the baseline has its own value and the
 *                  question is whether ours sits near it, not whether it is small.
 *   colour error   mean absolute RGB difference inside the intersection, 0-1
 */
import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const A = process.argv[2] ?? 'work/cmp/code';
const B = process.argv[3] ?? 'work/cmp/glb';
const VIEWS = ['front', 'profile-left', 'profile-right', 'rear', 'orbit-plus-35', 'orbit-minus-35'];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setContent('<canvas id="a"></canvas><canvas id="b"></canvas>');

const dataUrl = (file) => `data:image/png;base64,${readFileSync(file).toString('base64')}`;

const rows = [];
for (const view of VIEWS) {
  const result = await page.evaluate(async ([srcA, srcB]) => {
    const load = (src) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = reject;
      image.src = src;
    });
    const [imageA, imageB] = await Promise.all([load(srcA), load(srcB)]);
    if (imageA.width !== imageB.width || imageA.height !== imageB.height) {
      return { error: `size ${imageA.width}x${imageA.height} vs ${imageB.width}x${imageB.height}` };
    }
    const width = imageA.width; const height = imageA.height;
    const pixels = (image) => {
      const canvas = document.createElement('canvas');
      canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(image, 0, 0);
      return ctx.getImageData(0, 0, width, height).data;
    };
    const pa = pixels(imageA); const pb = pixels(imageB);

    // The declared background is #0f0f0f. A tolerance of 12 keeps the near-black rim of the figure in
    // the silhouette while keeping compression noise out of it.
    const isFigure = (p, i) => Math.abs(p[i] - 15) > 12 || Math.abs(p[i + 1] - 15) > 12 || Math.abs(p[i + 2] - 15) > 12;
    const luma = (p, i) => 0.2126 * p[i] + 0.7152 * p[i + 1] + 0.0722 * p[i + 2];

    let both = 0; let either = 0; let colourSum = 0;
    const maskA = new Uint8Array(width * height);
    const maskB = new Uint8Array(width * height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const n = y * width + x; const i = n * 4;
        const fa = isFigure(pa, i); const fb = isFigure(pb, i);
        maskA[n] = fa ? 1 : 0; maskB[n] = fb ? 1 : 0;
        if (fa || fb) either += 1;
        if (fa && fb) {
          both += 1;
          colourSum += (Math.abs(pa[i] - pb[i]) + Math.abs(pa[i + 1] - pb[i + 1]) + Math.abs(pa[i + 2] - pb[i + 2])) / 3;
        }
      }
    }

    const laplacian = (p, mask) => {
      let sum = 0; let count = 0;
      for (let y = 1; y < height - 1; y += 1) {
        for (let x = 1; x < width - 1; x += 1) {
          const n = y * width + x;
          if (!mask[n]) continue;
          const i = n * 4;
          const value = 4 * luma(p, i)
            - luma(p, i - 4) - luma(p, i + 4)
            - luma(p, i - width * 4) - luma(p, i + width * 4);
          sum += Math.abs(value); count += 1;
        }
      }
      return count ? sum / count : 0;
    };

    return {
      iou: both / Math.max(either, 1),
      colourError: colourSum / Math.max(both, 1) / 255,
      noiseA: laplacian(pa, maskA),
      noiseB: laplacian(pb, maskB),
      pixelsA: maskA.reduce((s, v) => s + v, 0),
      pixelsB: maskB.reduce((s, v) => s + v, 0),
    };
  }, [dataUrl(path.join(A, `${view}.png`)), dataUrl(path.join(B, `${view}.png`))]);

  if (result.error) { console.log(`${view.padEnd(16)} ${result.error}`); continue; }
  rows.push({ view, ...result });
  console.log(`${view.padEnd(16)} IoU ${result.iou.toFixed(4)}   colour ${result.colourError.toFixed(4)}   `
    + `noise ours ${result.noiseA.toFixed(2)} / baseline ${result.noiseB.toFixed(2)}   `
    + `px ${result.pixelsA.toLocaleString()} vs ${result.pixelsB.toLocaleString()}`);
}
const mean = (key) => rows.reduce((s, r) => s + r[key], 0) / Math.max(rows.length, 1);
console.log(`\nmean over ${rows.length} views: IoU ${mean('iou').toFixed(4)}   colour ${mean('colourError').toFixed(4)}`
  + `   noise ours ${mean('noiseA').toFixed(2)} / baseline ${mean('noiseB').toFixed(2)}`);
writeFileSync('work/compare-views.json', JSON.stringify({ a: A, b: B, rows }, null, 1));
await browser.close();
