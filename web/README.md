# Glass Logo Studio

Upload a logo, icon or image and get a true-to-size 3D glass version rendered with Three.js.
Built with Nuxt 4 on top of the [img2threejs](../README.md) skill's "shape extrude" pattern.
UI: Tailwind v4 + [shadcn-vue](https://www.shadcn-vue.com) (Reka UI) components, Motion for Vue for animation.

To add more shadcn-vue components: `npx shadcn-vue@latest add <name>` (they land in `app/components/ui`).

## Run

```bash
pnpm install
pnpm dev        # http://localhost:3000
pnpm build      # static client build in .output/public
```

## How it works

1. **Rasterize** — the file (PNG, JPG, WebP or SVG) is drawn onto a canvas at 512 to 2048 px.
2. **Mask** — a scalar field is built from dark pixels, light pixels or the alpha channel.
   `Auto` tries each, drops regions that touch the image border (page background), and keeps
   the smallest meaningful region. For a white glyph on a black rounded square that is the glyph.
3. **Trace** — `d3-contour` cuts the field at the threshold with sub-pixel interpolation, giving
   exterior rings and holes. A Ramer–Douglas–Peucker pass removes redundant points.
4. **Extrude** — each ring becomes a `THREE.Shape` and is extruded with an inward bevel
   (`bevelOffset = -bevel`) so the outline stays exactly on the traced silhouette.
   The mesh is built in millimetres: set the width and the height follows the source aspect.
5. **Glass** — `MeshTransmissionMaterial` from `@pmndrs/vanilla` renders the scene twice
   (back faces, then front faces) so rims refract the glass behind them. A small shader patch
   blends transmission toward the environment reflection at grazing angles to fake total
   internal reflection, which is what makes the rims go dark like real glass. Iridescence adds
   the blue/orange fringe.
6. **Metal & solid** — a second finish mode swaps in a single-pass `MeshPhysicalMaterial` (gold, rose gold,
   chrome, brushed, copper, titanium, black lacquer, ceramic). The studio environment is a gradient dome plus
   softboxes so metals read their shape; a blurred silhouette behind the model acts as a contact shadow.
7. **Export** — GLB (metres, KHR transmission/ior/iridescence), binary STL (millimetres) and a
   2× PNG of the current view.

## Files

| Path | Purpose |
| --- | --- |
| `app/utils/trace.ts` | image → mask → contours → simplified polygons |
| `app/utils/glassScene.ts` | renderer, studio environment, extrusion, glass material, exports |
| `app/components/GlassViewer.vue` | canvas host, resize, export downloads |
| `app/components/ControlPanel.vue` | sidebar: upload, trace, dimensions, glass presets, scene |
| `app/components/SliderField.vue` | shadcn Slider + click-to-edit value, scroll-wheel nudge, reset dot |
| `app/components/ColorField.vue` | popover HSV colour picker with hex input and swatches |
| `app/components/SectionGroup.vue` | collapsible sidebar section with animated height and reset |
| `app/components/ui/*` | generated shadcn-vue primitives |
| `app/components/Icon.vue` | inline Lucide-style stroke icons |
| `app/app.vue` | layout, toolbar (fit, rotate, exports), HUD, empty state, shortcuts `F` / `R` |
| `public/samples/rueya.png` | sample logo |
