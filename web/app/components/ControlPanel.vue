<script setup lang="ts">
import { Switch } from '@/components/ui/switch'
import { MATERIAL_PRESETS, DEFAULT_GLASS_PARAMS, type Finish, type GlassParams } from '~/utils/glassScene'
import { DEFAULT_TRACE_OPTIONS, toSvgPath, type TraceOptions, type TraceResult } from '~/utils/trace'

const props = defineProps<{
  trace: TraceResult | null
  traceOpts: TraceOptions
  glass: GlassParams
  heightMm: number
  fileName: string
  busy: boolean
  error: string
}>()
const emit = defineEmits<{ pick: [Event]; sample: []; clear: []; 'set-height': [number] }>()

const D = DEFAULT_GLASS_PARAMS
const T = DEFAULT_TRACE_OPTIONS

const svgPath = computed(() => (props.trace ? toSvgPath(props.trace) : ''))
const viewBox = computed(() => {
  const t = props.trace
  if (!t) return '0 0 1 1'
  const pad = Math.max(t.bbox.maxX - t.bbox.minX, t.bbox.maxY - t.bbox.minY) * 0.08
  return `${t.bbox.minX - pad} ${t.bbox.minY - pad} ${t.bbox.maxX - t.bbox.minX + pad * 2} ${t.bbox.maxY - t.bbox.minY + pad * 2}`
})

const activePreset = computed(() => {
  const p = props.glass
  return MATERIAL_PRESETS.find(pr => Object.entries(pr.values).every(([k, v]) => p[k as keyof GlassParams] === v))?.id ?? null
})
const presetTab = ref<Finish>(props.glass.finish)
watch(() => props.glass.finish, f => (presetTab.value = f))
const visiblePresets = computed(() => MATERIAL_PRESETS.filter(p => p.group === presetTab.value))
function applyPreset(id: string) {
  const pr = MATERIAL_PRESETS.find(p => p.id === id)
  if (pr) Object.assign(props.glass, pr.values)
}
function switchFinish(f: Finish) {
  presetTab.value = f
  if (props.glass.finish !== f) applyPreset(f === 'metal' ? 'gold' : 'clear')
}
const isGlass = computed(() => props.glass.finish === 'glass')

function pick<K extends keyof GlassParams>(...keys: K[]) {
  const out: Partial<GlassParams> = {}
  for (const k of keys) out[k] = D[k]
  Object.assign(props.glass, out)
}
const modeLabel: Record<string, string> = { dark: 'dark pixels', light: 'light pixels', alpha: 'alpha' }
const metalSwatches = ['#e4b84a', '#d9a08a', '#f2f3f5', '#d8d9dc', '#c8734a', '#5f636b', '#15161a', '#f4f4f2', '#2c5cff', '#c0392b']
const bgSwatches = ['#fafaf9', '#f4f4f2', '#ffffff', '#ecebe7', '#e6e9f0', '#dfe6f5', '#f3e9e2', '#1c1c1f', '#0b0b0d', '#2b2f3a', '#3b2a4a']
</script>

<template>
  <aside class="flex h-full flex-col border-r border-border/70" :style="{ width: 'var(--sidebar-w)', background: 'var(--sidebar-bg)' }">
    <!-- Workspace header -->
    <header class="flex h-[52px] items-center gap-2 px-4">
      <span class="flex size-5 items-center justify-center rounded-[5px] bg-foreground text-background">
        <Icon name="logo" :size="11" />
      </span>
      <span class="text-[13.5px] font-semibold tracking-[-0.01em]">Glass Studio</span>
      <span class="flex-1" />
      <label class="flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition hover:bg-[var(--row-hover)] hover:text-foreground" title="Open image">
        <Icon name="upload" :size="15" />
        <input type="file" accept="image/*,.svg" hidden @change="emit('pick', $event)">
      </label>
    </header>

    <div class="flex-1 overflow-y-auto pb-6 [scrollbar-width:thin]">
      <!-- Source -->
      <section class="px-4 pt-1">
        <span class="block h-6 text-[12px] font-medium text-muted-foreground">Source</span>
        <label
          class="group relative mt-2 flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2.5 transition-colors hover:bg-[var(--row-hover)]"
          :class="{ 'bg-card shadow-[0_0_0_1px_var(--border)]': trace }"
        >
          <input type="file" accept="image/*,.svg" hidden @change="emit('pick', $event)">
          <template v-if="trace">
            <svg class="size-9 shrink-0 text-foreground" :viewBox="viewBox" preserveAspectRatio="xMidYMid meet">
              <path :d="svgPath" fill="currentColor" fill-rule="evenodd" />
            </svg>
            <div class="min-w-0 flex-1">
              <div class="truncate text-[13px] font-medium" :title="fileName">{{ fileName }}</div>
              <div class="mt-px text-[11.5px] text-muted-foreground">
                {{ trace.polygons.length }} shape{{ trace.polygons.length === 1 ? '' : 's' }} · {{ trace.vertexCount }} pts · {{ modeLabel[trace.resolvedMode] }}
              </div>
            </div>
            <button
              class="flex size-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-[var(--row-hover)] hover:text-foreground group-hover:opacity-100"
              title="Remove" @click.prevent="emit('clear')"
            ><Icon name="x" :size="13" /></button>
          </template>
          <template v-else>
            <span class="flex size-9 shrink-0 items-center justify-center rounded-md bg-card text-muted-foreground shadow-[0_0_0_1px_var(--border)]">
              <Icon name="image" :size="16" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="text-[13px] font-medium">Choose an image</div>
              <div class="mt-px text-[11.5px] text-muted-foreground">PNG, JPG, WebP, SVG · drop or paste</div>
            </div>
          </template>
          <span v-if="busy" class="absolute top-2.5 right-2.5 size-3 animate-spin rounded-full border-2 border-muted border-t-foreground" />
        </label>
        <button class="mt-1 flex h-8 w-full items-center gap-2.5 rounded-lg px-2.5 text-[13px] text-muted-foreground transition hover:bg-[var(--row-hover)] hover:text-foreground" @click="emit('sample')">
          <Icon name="sparkle" :size="14" class="opacity-70" /> Try the sample logo
        </button>
        <p v-if="error" class="mt-2 flex items-center gap-1.5 px-1 text-[12px] text-destructive"><Icon name="info" :size="13" /> {{ error }}</p>
      </section>

      <!-- Trace -->
      <SectionGroup title="Trace" resettable :count="trace ? `${trace.polygons.length} shapes` : undefined" @reset="Object.assign(traceOpts, T)">
        <div class="seg">
          <button v-for="m in (['auto', 'dark', 'light', 'alpha'] as const)" :key="m" :class="{ on: traceOpts.mode === m }" @click="traceOpts.mode = m">{{ m === 'auto' ? 'Auto' : m === 'alpha' ? 'Alpha' : m === 'dark' ? 'Dark' : 'Light' }}</button>
        </div>
        <SliderField v-model="traceOpts.threshold" label="Threshold" :min="0.1" :max="0.9" :step="0.01" :default-value="T.threshold" />
        <SliderField v-model="traceOpts.simplify" label="Smoothing" :min="0" :max="4" :step="0.1" unit="px" :decimals="1" :default-value="T.simplify" />
        <SliderField v-model="traceOpts.minRegion" label="Ignore specks" :min="0" :max="0.05" :step="0.001" :decimals="3" :default-value="T.minRegion" />
        <div class="row">
          <span>Fill holes</span>
          <Switch class="scale-90" :model-value="traceOpts.fillHoles" @update:model-value="v => (traceOpts.fillHoles = v)" />
        </div>
        <div class="row">
          <span>Resolution</span>
          <div class="seg w-[150px]">
            <button v-for="r in [512, 1024, 2048]" :key="r" class="font-mono" :class="{ on: traceOpts.resolution === r }" @click="traceOpts.resolution = r">{{ r }}</button>
          </div>
        </div>
      </SectionGroup>

      <!-- Dimensions -->
      <SectionGroup title="Dimensions" resettable @reset="pick('widthMm', 'depthMm', 'bevelMm', 'bevelSegments')">
        <div class="grid grid-cols-3 gap-1.5">
          <label v-for="d in ([['W', 'widthMm'], ['H', 'height'], ['D', 'depthMm']] as const)" :key="d[0]" class="dim">
            <span>{{ d[0] }}</span>
            <input
              v-if="d[1] === 'height'"
              :value="+heightMm.toFixed(1)" type="number" min="1" step="1"
              @change="emit('set-height', +($event.target as HTMLInputElement).value)"
            >
            <input v-else v-model.number="glass[d[1]]" type="number" :min="d[1] === 'depthMm' ? 0.5 : 1" :step="d[1] === 'depthMm' ? 0.5 : 1">
            <em>mm</em>
          </label>
        </div>
        <SliderField v-model="glass.bevelMm" label="Edge radius" :min="0" :max="Math.max(0.5, glass.depthMm / 2)" :step="0.1" unit="mm" :decimals="1" :default-value="D.bevelMm" />
        <SliderField v-model="glass.bevelSegments" label="Edge smoothness" :min="1" :max="14" :step="1" :decimals="0" :default-value="D.bevelSegments" />
      </SectionGroup>

      <!-- Material -->
      <SectionGroup title="Material" resettable @reset="applyPreset('clear')">
        <div class="seg">
          <button :class="{ on: presetTab === 'glass' }" @click="switchFinish('glass')">Glass</button>
          <button :class="{ on: presetTab === 'metal' }" @click="switchFinish('metal')">Metal &amp; solid</button>
        </div>
        <div class="-mx-1.5 grid grid-cols-2 gap-x-1 gap-y-px">
          <button
            v-for="p in visiblePresets" :key="p.id"
            class="flex h-8 items-center gap-2.5 rounded-md px-2 text-left text-[13px] transition-colors"
            :class="activePreset === p.id ? 'bg-card text-foreground shadow-[0_0_0_1px_var(--border)]' : 'text-foreground/85 hover:bg-[var(--row-hover)]'"
            @click="applyPreset(p.id)"
          >
            <span class="size-4 shrink-0 rounded-full border border-black/10 shadow-[inset_0_1px_1px_rgba(255,255,255,0.5)]" :style="{ background: p.swatch }" />
            <span class="truncate">{{ p.label }}</span>
          </button>
        </div>

        <template v-if="isGlass">
          <SliderField v-model="glass.ior" label="Refraction" :min="1" :max="2.33" :step="0.01" :default-value="D.ior" />
          <SliderField v-model="glass.dispersion" label="Dispersion" :min="0" :max="0.3" :step="0.005" :decimals="3" :default-value="D.dispersion" />
          <SliderField v-model="glass.clarity" label="Clarity" :min="0.3" :max="1" :step="0.01" :default-value="D.clarity" />
          <SliderField v-model="glass.roughness" label="Frost" :min="0" :max="0.6" :step="0.01" :default-value="D.roughness" />
          <SliderField v-model="glass.blur" label="Blur" :min="0" :max="1" :step="0.01" :default-value="D.blur" />
          <SliderField v-model="glass.tir" label="Edge darkening" :min="0" :max="1" :step="0.01" :default-value="D.tir" />
          <SliderField v-model="glass.edgeChroma" label="Edge chroma" :min="0" :max="1" :step="0.01" :default-value="D.edgeChroma" />
          <SliderField v-model="glass.clearcoat" label="Polish" :min="0" :max="1" :step="0.01" :default-value="D.clearcoat" />
          <ColorField v-model="glass.tint" label="Tint" />
          <SliderField v-model="glass.attenuationMm" label="Tint depth" :min="2" :max="600" :step="1" unit="mm" :decimals="0" :default-value="D.attenuationMm" />
        </template>
        <template v-else>
          <ColorField v-model="glass.tint" label="Color" :swatches="metalSwatches" />
          <SliderField v-model="glass.metalness" label="Metalness" :min="0" :max="1" :step="0.01" :default-value="1" />
          <SliderField v-model="glass.roughness" label="Roughness" :min="0" :max="1" :step="0.01" :default-value="0.22" />
          <SliderField v-model="glass.clearcoat" label="Lacquer" :min="0" :max="1" :step="0.01" :default-value="0" />
        </template>
      </SectionGroup>

      <!-- Scene -->
      <SectionGroup title="Scene" :default-open="false" resettable @reset="pick('background', 'shadow', 'vignette', 'envIntensity', 'exposure', 'fov', 'autoRotate', 'rotateSpeed')">
        <ColorField v-model="glass.background" label="Background" :swatches="bgSwatches" />
        <SliderField v-model="glass.shadow" label="Shadow" :min="0" :max="1" :step="0.01" :default-value="D.shadow" />
        <SliderField v-model="glass.vignette" label="Vignette" :min="0" :max="0.6" :step="0.01" :default-value="D.vignette" />
        <SliderField v-model="glass.envIntensity" label="Studio light" :min="0" :max="3" :step="0.05" :default-value="D.envIntensity" />
        <SliderField v-model="glass.exposure" label="Exposure" :min="0.4" :max="2" :step="0.01" :default-value="D.exposure" />
        <SliderField v-model="glass.fov" label="Lens" :min="15" :max="60" :step="1" :decimals="0" unit="°" :default-value="D.fov" />
        <div class="row">
          <span>Auto rotate</span>
          <Switch class="scale-90" :model-value="glass.autoRotate" @update:model-value="v => (glass.autoRotate = v)" />
        </div>
        <SliderField v-if="glass.autoRotate" v-model="glass.rotateSpeed" label="Speed" :min="0.2" :max="4" :step="0.1" :decimals="1" :default-value="D.rotateSpeed" />
      </SectionGroup>
    </div>
  </aside>
</template>

<style scoped>
@reference "~/assets/main.css";

.row { @apply flex h-7 items-center justify-between; }
.row > span { @apply text-[13px] text-foreground/80; }

.seg { @apply flex rounded-md p-0.5; background: rgba(0, 0, 0, 0.05); }
.seg button {
  @apply h-6 flex-1 rounded-[5px] px-2 text-[12px] font-medium text-muted-foreground transition-colors;
}
.seg button:hover { color: var(--foreground); }
.seg button.on { @apply bg-card text-foreground; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(0, 0, 0, 0.04); }

.dim { @apply flex h-8 items-center gap-1.5 rounded-md bg-card px-2; box-shadow: 0 0 0 1px var(--border); }
.dim:focus-within { box-shadow: 0 0 0 1px var(--ring); }
.dim > span { @apply text-[10.5px] font-semibold text-muted-foreground; }
.dim input { @apply w-full min-w-0 bg-transparent text-right font-mono text-[12px] outline-none; }
.dim em { @apply text-[10px] not-italic text-muted-foreground; }
</style>
