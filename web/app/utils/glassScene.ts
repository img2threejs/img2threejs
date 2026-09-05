import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js'
import { STLExporter } from 'three/addons/exporters/STLExporter.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { MeshTransmissionMaterial } from '@pmndrs/vanilla'
import type { TraceResult } from './trace'

/** All units are millimetres so exported files carry real-world dimensions. */
export type Finish = 'glass' | 'metal'

export interface GlassParams {
  /** Glass renders with refraction passes; metal is a single-pass PBR surface. */
  finish: Finish
  /** Metal finish only: 1 = pure metal, lower mixes toward a painted/ceramic solid. */
  metalness: number
  /** Opacity of the soft shadow cast on the backdrop (0..1). */
  shadow: number
  widthMm: number
  depthMm: number
  bevelMm: number
  bevelSegments: number
  ior: number
  dispersion: number
  roughness: number
  clearcoat: number
  /** Thin-film fringe strength on grazing edges (0..1). */
  edgeChroma: number
  /** How dark the rims go through fake total internal reflection (0..1). */
  tir: number
  /** Share of light passing through (0..1); lower reads as milky glass. */
  clarity: number
  /** Directional blur of the refraction (0..1). */
  blur: number
  tint: string
  attenuationMm: number
  envIntensity: number
  background: string
  /** How much darker the backdrop gets toward the corners (0..1). */
  vignette: number
  exposure: number
  fov: number
  autoRotate: boolean
  rotateSpeed: number
}

export const DEFAULT_GLASS_PARAMS: GlassParams = {
  finish: 'glass',
  metalness: 1,
  shadow: 0.35,
  widthMm: 100,
  depthMm: 12,
  bevelMm: 3.2,
  bevelSegments: 10,
  ior: 1.52,
  dispersion: 0.08,
  roughness: 0.08,
  clearcoat: 1,
  edgeChroma: 0.3,
  tir: 0.85,
  clarity: 1,
  blur: 0.15,
  tint: '#ffffff',
  attenuationMm: 400,
  envIntensity: 1.5,
  background: '#fafaf9',
  vignette: 0.2,
  exposure: 1,
  fov: 28,
  autoRotate: true,
  rotateSpeed: 1.2,
}

/**
 * A dark studio with a few bright softboxes. High contrast is what makes glass read as glass:
 * bevels go dark where they mirror the room and flare where they catch a strip light.
 */
function createStudioEnvironment(): THREE.Scene {
  const scene = new THREE.Scene()
  // Gradient dome: dark floor, mid-grey horizon, softer ceiling. Metals read their shape from
  // this gradient; a flat black room leaves them looking like cut-outs.
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(30, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      uniforms: {},
      vertexShader: 'varying vec3 vP; void main(){ vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
      fragmentShader: `varying vec3 vP; void main(){
        float t = clamp(vP.y / 30.0 * 0.5 + 0.5, 0.0, 1.0);
        vec3 floor = vec3(0.03, 0.03, 0.035);
        vec3 horizon = vec3(0.22, 0.22, 0.245);
        vec3 ceil = vec3(0.75, 0.76, 0.80);
        vec3 c = t < 0.5 ? mix(floor, horizon, smoothstep(0.0, 0.5, t)) : mix(horizon, ceil, smoothstep(0.5, 1.0, t));
        gl_FragColor = vec4(c, 1.0);
      }`,
    }),
  )
  scene.add(dome)
  const light = (w: number, h: number, color: number, intensity: number, pos: [number, number, number], look: [number, number, number] = [0, 0, 0]) => {
    const m = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(intensity), side: THREE.DoubleSide }),
    )
    m.position.set(...pos)
    m.lookAt(...look)
    scene.add(m)
  }
  light(14, 3, 0xffffff, 6, [0, 14, 4])          // long top softbox: main horizontal highlight
  light(2.5, 16, 0xffe2c0, 5, [-14, 2, 6])       // warm tall strip on the left
  light(2.5, 16, 0xc4d8ff, 5, [14, 2, 6])        // cool tall strip on the right
  light(10, 2, 0xffffff, 2.5, [0, -12, 8])       // faint fill from below
  light(6, 6, 0xffffff, 1.2, [0, 0, -16])        // back card seen through the glass
  light(16, 10, 0xffffff, 2.6, [0, 5, 18])       // broad front softbox: sheen on the face
  light(4, 1.2, 0xffffff, 8, [-6, 9, 10])        // small hard key: crisp specular streak
  return scene
}

export interface ModelDimensions {
  widthMm: number
  heightMm: number
  depthMm: number
  triangles: number
}

export class GlassStudio {
  readonly renderer: THREE.WebGLRenderer
  readonly scene = new THREE.Scene()
  readonly camera: THREE.PerspectiveCamera
  readonly controls: OrbitControls
  readonly material: MeshTransmissionMaterial
  private fboMain: THREE.WebGLRenderTarget
  private fboBack: THREE.WebGLRenderTarget
  private clock = new THREE.Clock()
  private mesh: THREE.Mesh | null = null
  private trace: TraceResult | null = null
  private params: GlassParams = { ...DEFAULT_GLASS_PARAMS }
  private raf = 0
  private pmrem: THREE.PMREMGenerator
  private disposed = false
  private key: THREE.DirectionalLight
  private rim: THREE.DirectionalLight
  private backdrop: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>
  private backdropCanvas = document.createElement('canvas')
  private shadowPlane: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>
  private shadowCanvas = document.createElement('canvas')
  readonly metal: THREE.MeshPhysicalMaterial

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.NeutralToneMapping
    this.renderer.toneMappingExposure = 1.0

    this.camera = new THREE.PerspectiveCamera(28, 1, 1, 10000)
    this.camera.position.set(-120, 60, 420)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.autoRotateSpeed = 1.2
    this.controls.minDistance = 20
    this.controls.maxDistance = 4000

    this.pmrem = new THREE.PMREMGenerator(this.renderer)
    this.scene.environment = this.pmrem.fromScene(createStudioEnvironment(), 0.02).texture
    this.renderer.transmissionResolutionScale = 1
    this.scene.background = new THREE.Color(this.params.background)

    // Two hard lights give the crisp specular streaks a studio glass shot has.
    this.key = new THREE.DirectionalLight(0xffffff, 2.2)
    this.key.position.set(-1, 1.2, 1.6)
    this.rim = new THREE.DirectionalLight(0xdfe9ff, 1.4)
    this.rim.position.set(1.4, -0.4, -1)
    this.scene.add(this.key, this.rim)

    // A soft vignette behind the model gives refraction and dispersion something to bend.
    this.backdropCanvas.width = this.backdropCanvas.height = 512
    const backdropTex = new THREE.CanvasTexture(this.backdropCanvas)
    backdropTex.colorSpace = THREE.SRGBColorSpace
    this.backdrop = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: backdropTex, toneMapped: false }),
    )
    this.backdrop.name = 'backdrop'
    this.scene.add(this.backdrop)
    this.paintBackdrop()

    // Soft contact shadow: a blurred silhouette of the traced shape, just behind the model.
    this.shadowCanvas.width = this.shadowCanvas.height = 1024
    const shadowTex = new THREE.CanvasTexture(this.shadowCanvas)
    this.shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false, opacity: this.params.shadow, toneMapped: false }),
    )
    this.shadowPlane.name = 'shadow'
    this.shadowPlane.renderOrder = -1
    this.shadowPlane.visible = false
    this.scene.add(this.shadowPlane)

    this.metal = new THREE.MeshPhysicalMaterial({
      color: new THREE.Color(this.params.tint),
      metalness: this.params.metalness,
      roughness: this.params.roughness,
      clearcoat: this.params.clearcoat,
      clearcoatRoughness: 0.08,
      envMapIntensity: this.params.envIntensity,
    })

    // drei's transmission material: renders the scene into its own buffer twice (back faces, then
    // front faces) so the rims refract the glass behind them instead of a flat background.
    this.material = new MeshTransmissionMaterial({
      samples: 12,
      transmissionSampler: false,
      chromaticAberration: this.params.dispersion,
      anisotropicBlur: this.params.blur,
      _transmission: this.params.clarity,
      thickness: this.params.depthMm,
      roughness: this.params.roughness,
      attenuationColor: new THREE.Color(this.params.tint),
      attenuationDistance: this.params.attenuationMm,
    })
    this.material.color.set(0xffffff)
    this.patchTotalInternalReflection()
    this.material.metalness = 0
    this.material.ior = this.params.ior
    this.material.clearcoat = this.params.clearcoat
    this.material.clearcoatRoughness = 0.04
    this.material.iridescence = this.params.edgeChroma
    this.material.iridescenceIOR = 1.3
    this.material.iridescenceThicknessRange = [120, 480]
    this.material.specularIntensity = 1
    this.material.envMapIntensity = this.params.envIntensity
    this.material.side = THREE.FrontSide

    const fboOpts: THREE.RenderTargetOptions = { type: THREE.HalfFloatType, samples: 4 }
    this.fboMain = new THREE.WebGLRenderTarget(1, 1, fboOpts)
    this.fboBack = new THREE.WebGLRenderTarget(1, 1, fboOpts)
    this.material.uniforms.buffer.value = this.fboMain.texture

    this.resize()
    this.loop()
    if (import.meta.dev) (window as unknown as { __studio: GlassStudio }).__studio = this
  }

  resize() {
    const w = this.canvas.clientWidth || 1
    const h = this.canvas.clientHeight || 1
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    const pr = this.renderer.getPixelRatio()
    const bw = Math.min(2048, Math.round(w * pr))
    const bh = Math.min(2048, Math.round(h * pr))
    this.fboMain.setSize(bw, bh)
    this.fboBack.setSize(Math.round(bw / 2), Math.round(bh / 2))
    this.placeBackdrop()
  }

  private loop = () => {
    if (this.disposed) return
    this.raf = requestAnimationFrame(this.loop)
    this.controls.update()
    this.placeBackdrop()
    this.renderPasses()
  }

  /** Back-face pass, main pass, then the visible pass, as drei's MeshTransmissionMaterial does. */
  private renderPasses() {
    const gl = this.renderer
    const m = this.material
    const mesh = this.mesh
    if (!mesh || this.params.finish === 'metal') {
      gl.setRenderTarget(null)
      gl.render(this.scene, this.camera)
      return
    }
    m.uniforms.time.value = this.clock.getElapsedTime()
    const oldTone = gl.toneMapping
    gl.toneMapping = THREE.NoToneMapping

    // 1. Scene without the glass -> fboBack.
    mesh.visible = false
    gl.setRenderTarget(this.fboBack)
    gl.render(this.scene, this.camera)

    // 2. Back faces refracting fboBack -> fboMain.
    mesh.visible = true
    m.uniforms.buffer.value = this.fboBack.texture
    m.thickness = this.params.depthMm * 0.5
    m.side = THREE.BackSide
    gl.setRenderTarget(this.fboMain)
    gl.render(this.scene, this.camera)

    // 3. Front faces refracting fboMain -> screen.
    m.uniforms.buffer.value = this.fboMain.texture
    m.thickness = this.params.depthMm
    m.side = THREE.FrontSide
    gl.toneMapping = oldTone
    gl.setRenderTarget(null)
    gl.render(this.scene, this.camera)
  }

  setTrace(trace: TraceResult | null) {
    this.trace = trace
    this.rebuild()
    this.frame()
  }

  setParams(next: Partial<GlassParams>) {
    const prev = this.params
    this.params = { ...prev, ...next }
    const p = this.params
    const geometryChanged =
      p.widthMm !== prev.widthMm || p.depthMm !== prev.depthMm ||
      p.bevelMm !== prev.bevelMm || p.bevelSegments !== prev.bevelSegments
    this.material.roughness = p.roughness
    this.material.ior = p.ior
    this.material.uniforms.chromaticAberration.value = p.dispersion
    this.material.clearcoat = p.clearcoat
    this.material.envMapIntensity = p.envIntensity
    this.material.attenuationColor.set(p.tint)
    // Base colour follows the tint so dark or saturated glass stays dark where clarity drops.
    this.material.color.set(p.tint).lerp(new THREE.Color(0xffffff), 0.7)
    this.material.attenuationDistance = p.attenuationMm
    this.material.thickness = p.depthMm
    ;(this.scene.background as THREE.Color).set(p.background)
    this.material.iridescence = p.edgeChroma
    ;(this.material as unknown as { tirUniforms: { tirStrength: { value: number } } }).tirUniforms.tirStrength.value = p.tir
    if (p.background !== prev.background || p.vignette !== prev.vignette) this.paintBackdrop()
    this.metal.color.set(p.tint)
    this.metal.metalness = p.metalness
    this.metal.roughness = p.roughness
    this.metal.clearcoat = p.clearcoat
    this.metal.envMapIntensity = p.envIntensity
    this.shadowPlane.material.opacity = p.shadow
    if (this.mesh) this.mesh.material = p.finish === 'metal' ? this.metal : this.material
    this.material.uniforms.anisotropicBlur.value = p.blur
    this.material.uniforms._transmission.value = p.clarity
    this.renderer.toneMappingExposure = p.exposure
    this.controls.autoRotate = p.autoRotate
    this.controls.autoRotateSpeed = p.rotateSpeed
    if (p.fov !== this.camera.fov) {
      this.camera.fov = p.fov
      this.camera.updateProjectionMatrix()
    }
    if (geometryChanged) this.rebuild()
  }

  /**
   * Screen-space refraction can only show what is behind the glass, so rims that should go
   * dark through total internal reflection stay white. Blend the transmitted light toward the
   * environment reflection as the view angle grazes the surface, which is where TIR happens.
   */
  private patchTotalInternalReflection() {
    const m = this.material
    const inner = m.onBeforeCompile
    const tirUniforms = { tirStrength: { value: this.params.tir }, tirPower: { value: 1.5 } }
    ;(m as unknown as { tirUniforms: typeof tirUniforms }).tirUniforms = tirUniforms
    m.onBeforeCompile = (shader, renderer) => {
      inner.call(m, shader, renderer)
      shader.uniforms.tirStrength = tirUniforms.tirStrength
      shader.uniforms.tirPower = tirUniforms.tirPower
      shader.fragmentShader = shader.fragmentShader
        .replace('uniform float chromaticAberration;', 'uniform float chromaticAberration;\nuniform float tirStrength;\nuniform float tirPower;')
        .replace(
          'totalDiffuse = mix( totalDiffuse, transmission.rgb, material.transmission );',
          `{
            float grazing = pow( 1.0 - saturate( dot( normal, geometryViewDir ) ), tirPower );
            float tir = saturate( grazing * tirStrength );
            #ifdef USE_ENVMAP
              vec3 tirRadiance = getIBLRadiance( geometryViewDir, normal, material.roughness ) * envMapIntensity;
            #else
              vec3 tirRadiance = vec3( 0.0 );
            #endif
            transmission.rgb = mix( transmission.rgb, tirRadiance, tir );
          }
          totalDiffuse = mix( totalDiffuse, transmission.rgb, material.transmission );`,
        )
    }
    m.customProgramCacheKey = () => 'glass-tir'
  }

  private paintBackdrop() {
    const c = this.backdropCanvas
    const ctx = c.getContext('2d')!
    const base = new THREE.Color(this.params.background)
    const edge = base.clone().multiplyScalar(1 - this.params.vignette)
    const g = ctx.createRadialGradient(c.width / 2, c.height * 0.45, c.width * 0.12, c.width / 2, c.height / 2, c.width * 0.75)
    g.addColorStop(0, `#${base.getHexString()}`)
    g.addColorStop(1, `#${edge.getHexString()}`)
    ctx.fillStyle = g
    ctx.fillRect(0, 0, c.width, c.height)
    ;(this.backdrop.material.map as THREE.CanvasTexture).needsUpdate = true
  }

  /** Keep the backdrop filling the view a fixed distance behind the model. */
  private placeBackdrop() {
    const dist = this.camera.position.length()
    const behind = dist * 0.9
    const tanV = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2))
    const h = 2 * tanV * (dist + behind) * 3
    this.backdrop.scale.set(h * Math.max(this.camera.aspect, 1), h, 1)
    this.backdrop.position.copy(this.camera.position).normalize().multiplyScalar(-behind)
    this.backdrop.lookAt(this.camera.position)
  }

  getDimensions(): ModelDimensions | null {
    if (!this.mesh || !this.trace) return null
    const box = new THREE.Box3().setFromObject(this.mesh)
    const size = box.getSize(new THREE.Vector3())
    const idx = this.mesh.geometry.getIndex()
    const triangles = idx ? idx.count / 3 : this.mesh.geometry.getAttribute('position').count / 3
    return { widthMm: size.x, heightMm: size.y, depthMm: size.z, triangles }
  }

  /** Build one extruded, bevelled solid from the traced polygons at exact millimetre size. */
  private rebuild() {
    if (this.mesh) {
      this.scene.remove(this.mesh)
      this.mesh.geometry.dispose()
      this.mesh = null
    }
    this.shadowPlane.visible = false
    const t = this.trace
    if (!t || !t.polygons.length) return
    const p = this.params

    const bw = t.bbox.maxX - t.bbox.minX
    const bh = t.bbox.maxY - t.bbox.minY
    const scale = p.widthMm / Math.max(bw, 1e-6)
    const cx = (t.bbox.minX + t.bbox.maxX) / 2
    const cy = (t.bbox.minY + t.bbox.maxY) / 2
    const toMm = ([x, y]: [number, number]) => new THREE.Vector2((x - cx) * scale, -(y - cy) * scale)

    // Bevel cannot exceed half the depth; with bevelOffset = -bevel the outline stays exact.
    const bevel = Math.max(0, Math.min(p.bevelMm, p.depthMm / 2 - 0.01))
    const coreDepth = Math.max(0.01, p.depthMm - 2 * bevel)

    const geometries: THREE.BufferGeometry[] = []
    for (const poly of t.polygons) {
      const shape = new THREE.Shape(poly.exterior.map(toMm))
      for (const hole of poly.holes) {
        const path = new THREE.Path(hole.map(toMm))
        shape.holes.push(path)
      }
      const geo = new THREE.ExtrudeGeometry(shape, {
        depth: coreDepth,
        curveSegments: 1,
        bevelEnabled: bevel > 0,
        bevelThickness: bevel,
        bevelSize: bevel,
        bevelOffset: -bevel,
        bevelSegments: p.bevelSegments,
      })
      geometries.push(geo)
    }
    const merged = geometries.length === 1 ? geometries[0]! : mergeGeometries(geometries, false)!
    if (merged !== geometries[0]) geometries.forEach(g => g.dispose())
    merged.computeBoundingBox()
    const bb = merged.boundingBox!
    // Centre on the origin so the model rotates around itself.
    merged.translate(-(bb.min.x + bb.max.x) / 2, -(bb.min.y + bb.max.y) / 2, -(bb.min.z + bb.max.z) / 2)
    merged.computeVertexNormals()

    this.mesh = new THREE.Mesh(merged, p.finish === 'metal' ? this.metal : this.material)
    this.mesh.name = 'glass-logo'
    this.scene.add(this.mesh)
    this.paintShadow(t, scale, cx, cy, bw, bh, p.depthMm)
  }

  /** Draw the traced silhouette blurred onto a plane slightly behind and below the model. */
  private paintShadow(t: TraceResult, scale: number, cx: number, cy: number, bw: number, bh: number, depth: number) {
    const c = this.shadowCanvas
    const ctx = c.getContext('2d')!
    const pad = 0.35
    const wMm = bw * scale * (1 + pad * 2)
    const hMm = bh * scale * (1 + pad * 2)
    const sx = c.width / wMm, sy = c.height / hMm
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.save()
    ctx.translate(c.width / 2, c.height / 2)
    ctx.filter = `blur(${Math.max(6, c.width * 0.02)}px)`
    ctx.fillStyle = 'rgba(0,0,0,1)'
    ctx.beginPath()
    for (const poly of t.polygons) {
      for (const ring of [poly.exterior, ...poly.holes]) {
        ring.forEach(([x, y], i) => {
          const px = (x - cx) * scale * sx, py = (y - cy) * scale * sy
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
        })
        ctx.closePath()
      }
    }
    ctx.fill('evenodd')
    ctx.restore()
    ;(this.shadowPlane.material.map as THREE.CanvasTexture).needsUpdate = true
    this.shadowPlane.scale.set(wMm, hMm, 1)
    // Light comes from the upper left, so the shadow falls slightly down and to the right.
    this.shadowPlane.position.set(wMm * 0.015, -hMm * 0.02, -depth / 2 - depth * 0.6)
    this.shadowPlane.visible = true
  }

  /** Named camera angles; `frame()` re-fits the distance. */
  setView(view: 'angle' | 'front' | 'side' | 'top') {
    const dirs = { angle: [-0.32, 0.14, 1], front: [0, 0, 1], side: [1, 0.05, 0.25], top: [0, 1, 0.001] } as const
    const d = dirs[view]
    this.viewDir.set(d[0], d[1], d[2]).normalize()
    this.frame()
  }

  private viewDir = new THREE.Vector3(-0.32, 0.14, 1).normalize()

  /** Move the camera to the current view direction at a distance that fits the model. */
  frame() {
    if (!this.mesh) return
    const box = new THREE.Box3().setFromObject(this.mesh)
    const size = box.getSize(new THREE.Vector3())
    const tanV = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2))
    const tanH = tanV * this.camera.aspect
    // Fit both axes, then leave room for the panel on the left and the HUD below.
    const dist = Math.max(size.y / 2 / tanV, size.x / 2 / tanH, size.z) * 1.55
    this.camera.position.copy(this.viewDir).multiplyScalar(dist)
    this.camera.near = Math.max(0.1, dist / 100)
    this.camera.far = dist * 20
    this.camera.updateProjectionMatrix()
    this.controls.target.set(0, 0, 0)
    this.controls.update()
    this.placeBackdrop()
  }

  async exportGLB(): Promise<Blob> {
    if (!this.mesh) throw new Error('Nothing to export')
    // glTF uses metres; scale the millimetre mesh down on a wrapper.
    const wrapper = new THREE.Group()
    wrapper.name = 'glass-logo'
    wrapper.scale.setScalar(0.001)
    // The screen-space material zeroes `transmission`; export a standard KHR glass material.
    const p = this.params
    const exportMaterial = p.finish === 'metal'
      ? new THREE.MeshPhysicalMaterial({ color: new THREE.Color(p.tint), metalness: p.metalness, roughness: p.roughness, clearcoat: p.clearcoat, clearcoatRoughness: 0.08 })
      : new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      metalness: 0,
      roughness: p.roughness,
      transmission: 1,
      thickness: p.depthMm * 0.001,
      ior: p.ior,
      dispersion: p.dispersion * 2,
      clearcoat: p.clearcoat,
      clearcoatRoughness: 0.04,
      iridescence: p.edgeChroma,
      attenuationColor: new THREE.Color(p.tint),
      attenuationDistance: p.attenuationMm * 0.001,
    })
    exportMaterial.name = p.finish
    const clone = new THREE.Mesh(this.mesh.geometry, exportMaterial)
    clone.name = this.mesh.name
    wrapper.add(clone)
    const exporter = new GLTFExporter()
    try {
      const result = await exporter.parseAsync(wrapper, { binary: true })
      return new Blob([result as ArrayBuffer], { type: 'model/gltf-binary' })
    } finally {
      exportMaterial.dispose()
    }
  }

  exportSTL(): Blob {
    if (!this.mesh) throw new Error('Nothing to export')
    const exporter = new STLExporter()
    const data = exporter.parse(this.mesh, { binary: true }) as DataView<ArrayBuffer>
    return new Blob([data], { type: 'model/stl' })
  }

  async screenshot(scale = 2): Promise<Blob> {
    const prevRatio = this.renderer.getPixelRatio()
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio * scale, 4))
    this.resize()
    this.renderPasses()
    const blob = await new Promise<Blob>((resolve, reject) =>
      this.canvas.toBlob(b => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png'),
    )
    this.renderer.setPixelRatio(prevRatio)
    this.resize()
    return blob
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.controls.dispose()
    this.mesh?.geometry.dispose()
    this.backdrop.geometry.dispose()
    this.backdrop.material.map?.dispose()
    this.backdrop.material.dispose()
    this.material.dispose()
    this.metal.dispose()
    this.shadowPlane.geometry.dispose()
    this.shadowPlane.material.map?.dispose()
    this.shadowPlane.material.dispose()
    this.fboMain.dispose()
    this.fboBack.dispose()
    this.pmrem.dispose()
    this.renderer.dispose()
  }
}

export interface MaterialPreset {
  id: string
  label: string
  group: Finish
  /** CSS background for the swatch. */
  swatch: string
  values: Partial<GlassParams>
}

const glass = (v: Partial<GlassParams>): Partial<GlassParams> => ({
  finish: 'glass', ior: 1.52, dispersion: 0.08, roughness: 0.08, clearcoat: 1, edgeChroma: 0.3, tir: 0.85,
  clarity: 1, blur: 0.15, tint: '#ffffff', attenuationMm: 400, ...v,
})
const metal = (v: Partial<GlassParams>): Partial<GlassParams> => ({
  finish: 'metal', metalness: 1, roughness: 0.22, clearcoat: 0, tint: '#ffffff', ...v,
})

/** One-click looks. Each sets every material key so switching presets is deterministic. */
export const MATERIAL_PRESETS: MaterialPreset[] = [
  { id: 'clear', label: 'Clear', group: 'glass', swatch: 'linear-gradient(135deg,#fff 0%,#dfe3ea 55%,#fff 100%)', values: glass({}) },
  { id: 'crystal', label: 'Crystal', group: 'glass', swatch: 'linear-gradient(135deg,#fff,#cfe1ff 45%,#ffd9c4 55%,#fff)', values: glass({ ior: 1.9, dispersion: 0.18, roughness: 0.02, edgeChroma: 0.6, tir: 1, blur: 0.05, attenuationMm: 600 }) },
  { id: 'frosted', label: 'Frosted', group: 'glass', swatch: 'linear-gradient(135deg,#f6f6f6,#dcdcdc)', values: glass({ ior: 1.45, dispersion: 0.03, roughness: 0.35, clearcoat: 0.4, edgeChroma: 0.1, tir: 0.5, clarity: 0.9, blur: 0.5 }) },
  { id: 'milk', label: 'Milk', group: 'glass', swatch: 'linear-gradient(135deg,#ffffff,#eef0f2)', values: glass({ roughness: 0.25, clarity: 0.55, blur: 0.6, edgeChroma: 0.05, tir: 0.4, clearcoat: 0.8 }) },
  { id: 'smoke', label: 'Smoke', group: 'glass', swatch: 'linear-gradient(135deg,#9a9a9e,#4a4a4f)', values: glass({ tint: '#3a3a40', attenuationMm: 18, edgeChroma: 0.15, tir: 0.9, dispersion: 0.05 }) },
  { id: 'obsidian', label: 'Obsidian', group: 'glass', swatch: 'linear-gradient(135deg,#3a3a3f,#0a0a0c)', values: glass({ tint: '#0b0b0d', attenuationMm: 2, roughness: 0.04, edgeChroma: 0.2, tir: 1, dispersion: 0.03 }) },
  { id: 'sapphire', label: 'Sapphire', group: 'glass', swatch: 'linear-gradient(135deg,#cfe0ff,#3d7bff)', values: glass({ tint: '#7fa8ff', attenuationMm: 45, edgeChroma: 0.2 }) },
  { id: 'emerald', label: 'Emerald', group: 'glass', swatch: 'linear-gradient(135deg,#c9f2dc,#1f9d6a)', values: glass({ tint: '#5fcf9a', attenuationMm: 40, dispersion: 0.1, ior: 1.58 }) },
  { id: 'amber', label: 'Amber', group: 'glass', swatch: 'linear-gradient(135deg,#ffe3b0,#e0851a)', values: glass({ tint: '#f4b054', attenuationMm: 36, dispersion: 0.06, edgeChroma: 0.1 }) },
  { id: 'rose', label: 'Rosé', group: 'glass', swatch: 'linear-gradient(135deg,#ffe4ec,#ff8fb0)', values: glass({ tint: '#ff9ab8', attenuationMm: 45, edgeChroma: 0.25 }) },
  { id: 'holo', label: 'Holo', group: 'glass', swatch: 'linear-gradient(135deg,#ffd1f0,#c8e7ff 40%,#d9ffe1 70%,#fff2c2)', values: glass({ edgeChroma: 1, dispersion: 0.3, ior: 1.7, tir: 0.7, blur: 0.1 }) },
  { id: 'gold', label: 'Gold', group: 'metal', swatch: 'linear-gradient(135deg,#fff1b8,#d4a017 55%,#8a6508)', values: metal({ tint: '#e4b84a', roughness: 0.2 }) },
  { id: 'rosegold', label: 'Rose gold', group: 'metal', swatch: 'linear-gradient(135deg,#ffe0d0,#d9946f 55%,#8e5a45)', values: metal({ tint: '#d9a08a', roughness: 0.22 }) },
  { id: 'chrome', label: 'Chrome', group: 'metal', swatch: 'linear-gradient(135deg,#ffffff,#b8bcc4 50%,#5d6169)', values: metal({ tint: '#f2f3f5', roughness: 0.06 }) },
  { id: 'silver', label: 'Brushed', group: 'metal', swatch: 'linear-gradient(135deg,#e6e7ea,#a9acb3 55%,#7d8188)', values: metal({ tint: '#d8d9dc', roughness: 0.42 }) },
  { id: 'copper', label: 'Copper', group: 'metal', swatch: 'linear-gradient(135deg,#ffc9a8,#c06a3a 55%,#6e3517)', values: metal({ tint: '#c8734a', roughness: 0.26 }) },
  { id: 'titanium', label: 'Titanium', group: 'metal', swatch: 'linear-gradient(135deg,#8f939b,#4f535a 55%,#2a2d32)', values: metal({ tint: '#5f636b', roughness: 0.34 }) },
  { id: 'black', label: 'Black', group: 'metal', swatch: 'linear-gradient(135deg,#3a3b40,#111114)', values: metal({ tint: '#15161a', metalness: 0.35, roughness: 0.32, clearcoat: 0.9 }) },
  { id: 'ceramic', label: 'Ceramic', group: 'metal', swatch: 'linear-gradient(135deg,#ffffff,#eceff3)', values: metal({ tint: '#f4f4f2', metalness: 0, roughness: 0.28, clearcoat: 1 }) },
]

/** @deprecated kept for older imports; use MATERIAL_PRESETS. */
export const GLASS_PRESETS = MATERIAL_PRESETS.filter(p => p.group === 'glass').map(p => ({ id: p.id, label: p.label, values: p.values }))
