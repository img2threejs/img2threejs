import { contours as d3Contours } from 'd3-contour'

export type SourceMode = 'auto' | 'dark' | 'light' | 'alpha'

export interface TraceOptions {
  /** Longest side of the working raster, in px. */
  resolution: number
  /** Which pixels count as the logo. */
  mode: SourceMode
  /** Iso value in [0,1] used to cut the grayscale field. */
  threshold: number
  /** Ramer–Douglas–Peucker tolerance in working-raster pixels. */
  simplify: number
  /** Regions smaller than this share of the traced area are dropped as noise (0..0.2). */
  minRegion: number
  /** Ignore holes (counters) inside shapes. */
  fillHoles: boolean
}

export type Ring = [number, number][]

/** One solid region: exterior ring + hole rings, in working-raster pixel space (y down). */
export interface TracedPolygon {
  exterior: Ring
  holes: Ring[]
}

export interface TraceResult {
  polygons: TracedPolygon[]
  /** Working raster size. */
  width: number
  height: number
  /** Bounding box of the traced polygons in raster px. */
  bbox: { minX: number; minY: number; maxX: number; maxY: number }
  /** Mode actually used (resolved when `auto`). */
  resolvedMode: Exclude<SourceMode, 'auto'>
  /** Fraction of the raster covered by the traced polygons. */
  coverage: number
  /** Total vertex count after simplification. */
  vertexCount: number
}

export const DEFAULT_TRACE_OPTIONS: TraceOptions = {
  resolution: 1024,
  mode: 'auto',
  threshold: 0.5,
  simplify: 0.6,
  minRegion: 0.002,
  fillHoles: false,
}

export async function loadImage(file: File): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file)
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('Could not decode image'))
      img.src = url
    })
  } finally {
    // Revoke after decode; the element keeps its bitmap.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

export function rasterize(img: HTMLImageElement, resolution: number): ImageData {
  const iw = img.naturalWidth || img.width
  const ih = img.naturalHeight || img.height
  const scale = resolution / Math.max(iw, ih)
  const w = Math.max(2, Math.round(iw * scale))
  const h = Math.max(2, Math.round(ih * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(img, 0, 0, w, h)
  return ctx.getImageData(0, 0, w, h)
}

/** Build a scalar field in [0,1] where 1 = "logo" for the given mode. */
function buildField(data: ImageData, mode: Exclude<SourceMode, 'auto'>): Float32Array {
  const { width, height } = data
  const px = data.data
  const field = new Float32Array(width * height)
  for (let i = 0, p = 0; i < field.length; i++, p += 4) {
    const r = px[p]!, g = px[p + 1]!, b = px[p + 2]!, a = px[p + 3]! / 255
    if (mode === 'alpha') {
      field[i] = a
      continue
    }
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    const v = mode === 'dark' ? 1 - lum : lum
    // Transparent pixels never count as logo for luminance modes.
    field[i] = v * a
  }
  return field
}

function hasUsefulAlpha(data: ImageData): boolean {
  const px = data.data
  let transparent = 0
  let opaque = 0
  for (let p = 3; p < px.length; p += 4) {
    if (px[p]! < 16) transparent++
    else if (px[p]! > 240) opaque++
  }
  const n = px.length / 4
  return transparent > n * 0.005 && opaque > n * 0.001
}

function ringArea(ring: Ring): number {
  let s = 0
  for (let i = 0, n = ring.length; i < n; i++) {
    const [x1, y1] = ring[i]!
    const [x2, y2] = ring[(i + 1) % n]!
    s += x1 * y2 - x2 * y1
  }
  return s / 2
}

function touchesBorder(ring: Ring, w: number, h: number): boolean {
  const eps = 0.51
  for (const [x, y] of ring) {
    if (x <= eps || y <= eps || x >= w - eps || y >= h - eps) return true
  }
  return false
}

/** Ramer–Douglas–Peucker on a closed ring. */
function simplifyRing(ring: Ring, tol: number): Ring {
  if (tol <= 0 || ring.length < 4) return ring
  const pts = ring[0]![0] === ring[ring.length - 1]![0] && ring[0]![1] === ring[ring.length - 1]![1]
    ? ring.slice(0, -1)
    : ring
  if (pts.length < 4) return pts
  // Anchor on the two farthest-apart points so the closed loop simplifies symmetrically.
  let a = 0, b = 0, best = -1
  for (let i = 0; i < pts.length; i++) {
    const d = dist2(pts[0]!, pts[i]!)
    if (d > best) { best = d; b = i }
  }
  const part1 = rdp(pts.slice(a, b + 1), tol)
  const part2 = rdp([...pts.slice(b), pts[0]!], tol)
  const out = [...part1.slice(0, -1), ...part2.slice(0, -1)]
  return out.length >= 3 ? out : pts
}

function dist2(p: [number, number], q: [number, number]): number {
  const dx = p[0] - q[0], dy = p[1] - q[1]
  return dx * dx + dy * dy
}

function rdp(pts: Ring, tol: number): Ring {
  if (pts.length < 3) return pts
  const first = pts[0]!, last = pts[pts.length - 1]!
  let idx = -1, maxD = 0
  for (let i = 1; i < pts.length - 1; i++) {
    const d = perpDist(pts[i]!, first, last)
    if (d > maxD) { maxD = d; idx = i }
  }
  if (maxD > tol && idx > 0) {
    const l = rdp(pts.slice(0, idx + 1), tol)
    const r = rdp(pts.slice(idx), tol)
    return [...l.slice(0, -1), ...r]
  }
  return [first, last]
}

function perpDist(p: [number, number], a: [number, number], b: [number, number]): number {
  const dx = b[0] - a[0], dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return Math.sqrt(dist2(p, a))
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2))
  const px = a[0] + t * dx, py = a[1] + t * dy
  return Math.sqrt(dist2(p, [px, py]))
}

interface Candidate {
  mode: Exclude<SourceMode, 'auto'>
  polygons: TracedPolygon[]
  area: number
}

function traceMode(
  data: ImageData,
  mode: Exclude<SourceMode, 'auto'>,
  threshold: number,
  dropBorder: boolean,
): Candidate {
  const { width, height } = data
  const field = buildField(data, mode)
  const gen = d3Contours().size([width, height]).smooth(true).thresholds([threshold])
  const [multi] = gen(Array.from(field))
  const polygons: TracedPolygon[] = []
  let area = 0
  if (multi) {
    for (const poly of multi.coordinates) {
      const exterior = poly[0] as Ring
      if (!exterior || exterior.length < 4) continue
      if (dropBorder && touchesBorder(exterior, width, height)) continue
      const holes = poly.slice(1) as Ring[]
      const a = Math.abs(ringArea(exterior)) - holes.reduce((s, h) => s + Math.abs(ringArea(h)), 0)
      if (a < 4) continue
      polygons.push({ exterior, holes })
      area += a
    }
  }
  return { mode, polygons, area }
}

export function traceImageData(data: ImageData, opts: TraceOptions): TraceResult {
  const { width, height } = data
  const total = width * height
  const minArea = total * 0.0015

  let candidates: Candidate[]
  if (opts.mode === 'auto') {
    const alphaOk = hasUsefulAlpha(data)
    const modes: Exclude<SourceMode, 'auto'>[] = alphaOk ? ['alpha', 'dark', 'light'] : ['dark', 'light']
    candidates = modes.map(m => traceMode(data, m, opts.threshold, m !== 'alpha'))
    // Fall back to keeping border-touching regions when a mode found nothing otherwise.
    candidates = candidates.map(c =>
      c.polygons.length === 0 && c.mode !== 'alpha' ? traceMode(data, c.mode, opts.threshold, false) : c,
    )
  } else {
    let c = traceMode(data, opts.mode, opts.threshold, opts.mode !== 'alpha')
    if (c.polygons.length === 0) c = traceMode(data, opts.mode, opts.threshold, false)
    candidates = [c]
  }

  // Auto: the logo is the smallest meaningful region that is not the page background.
  const viable = candidates.filter(c => c.area >= minArea)
  const pool = viable.length ? viable : candidates.filter(c => c.polygons.length)
  const chosen = pool.length
    ? pool.reduce((best, c) => (c.area < best.area ? c : best))
    : { mode: opts.mode === 'auto' ? 'dark' as const : opts.mode, polygons: [], area: 0 }

  const dropBelow = chosen.area * opts.minRegion
  const polygons = chosen.polygons
    .filter(p => Math.abs(ringArea(p.exterior)) >= dropBelow)
    .map(p => ({
      exterior: simplifyRing(p.exterior, opts.simplify),
      holes: opts.fillHoles
        ? []
        : p.holes
          .filter(h => Math.abs(ringArea(h)) >= dropBelow)
          .map(h => simplifyRing(h, opts.simplify))
          .filter(h => h.length >= 3),
    }))

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  let vertexCount = 0
  for (const p of polygons) {
    for (const ring of [p.exterior, ...p.holes]) {
      vertexCount += ring.length
      for (const [x, y] of ring) {
        if (x < minX) minX = x
        if (y < minY) minY = y
        if (x > maxX) maxX = x
        if (y > maxY) maxY = y
      }
    }
  }
  if (!polygons.length) { minX = minY = 0; maxX = width; maxY = height }

  return {
    polygons,
    width,
    height,
    bbox: { minX, minY, maxX, maxY },
    resolvedMode: chosen.mode,
    coverage: chosen.area / total,
    vertexCount,
  }
}

/** Serialise the traced polygons as an SVG path (raster px space) for previews. */
export function toSvgPath(result: TraceResult): string {
  const parts: string[] = []
  for (const p of result.polygons) {
    for (const ring of [p.exterior, ...p.holes]) {
      parts.push(ring.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`).join('') + 'Z')
    }
  }
  return parts.join(' ')
}
