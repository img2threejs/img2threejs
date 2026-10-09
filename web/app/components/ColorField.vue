<script setup lang="ts">
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const props = defineProps<{ label: string; modelValue: string; swatches?: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const defaultSwatches = ['#ffffff', '#f5f5f4', '#e6e9f0', '#cfe0ff', '#7fb2ff', '#b8f0e3', '#ffe2c0', '#ffb3b3', '#d9c8ff', '#17181b']
const palette = computed(() => props.swatches ?? defaultSwatches)

// --- colour maths -------------------------------------------------------------
function hexToHsv(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return [0, 0, 1]
  const n = parseInt(m[1]!, 16)
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
  let h = 0
  if (d) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h = (h * 60 + 360) % 360
  }
  return [h, max ? d / max : 0, max]
}
function hsvToHex(h: number, s: number, v: number): string {
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c
  let r = 0, g = 0, b = 0
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  const to = (n: number) => Math.round((n + m) * 255).toString(16).padStart(2, '0')
  return `#${to(r)}${to(g)}${to(b)}`
}

const hsv = ref<[number, number, number]>(hexToHsv(props.modelValue))
watch(() => props.modelValue, v => {
  if (v.toLowerCase() !== hsvToHex(...hsv.value).toLowerCase()) hsv.value = hexToHsv(v)
})
function commit() { emit('update:modelValue', hsvToHex(...hsv.value)) }

const hueHex = computed(() => hsvToHex(hsv.value[0], 1, 1))
const hexDraft = ref(props.modelValue)
watch(() => props.modelValue, v => (hexDraft.value = v))
function commitHex() {
  const v = hexDraft.value.startsWith('#') ? hexDraft.value : `#${hexDraft.value}`
  if (/^#[0-9a-f]{6}$/i.test(v)) emit('update:modelValue', v.toLowerCase())
  else hexDraft.value = props.modelValue
}

// --- pointer handling on the saturation/value square and hue strip ------------
function drag(e: PointerEvent, onMove: (x: number, y: number) => void) {
  const el = e.currentTarget as HTMLElement
  el.setPointerCapture(e.pointerId)
  const rect = el.getBoundingClientRect()
  const update = (ev: PointerEvent) => {
    const x = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width))
    const y = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height))
    onMove(x, y)
    commit()
  }
  update(e)
  const stop = () => {
    el.removeEventListener('pointermove', update)
    el.removeEventListener('pointerup', stop)
  }
  el.addEventListener('pointermove', update)
  el.addEventListener('pointerup', stop)
}
const onSquare = (e: PointerEvent) => drag(e, (x, y) => { hsv.value = [hsv.value[0], x, 1 - y] })
const onHue = (e: PointerEvent) => drag(e, x => { hsv.value = [x * 359.99, hsv.value[1], hsv.value[2]] })
</script>

<template>
  <div class="flex h-7 items-center justify-between">
    <span class="text-[13px] text-foreground/80">{{ label }}</span>
    <Popover>
      <PopoverTrigger as-child>
        <button class="group flex h-7 items-center gap-2 rounded-md pr-2 pl-1 transition hover:bg-[var(--row-hover)]">
          <span class="size-4.5 rounded-full border border-black/10 shadow-[inset_0_1px_1px_rgba(255,255,255,0.4)]" :style="{ background: modelValue }" />
          <span class="font-mono text-[11.5px] text-muted-foreground group-hover:text-foreground">{{ modelValue }}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" :side-offset="6" class="w-56 rounded-lg p-3 shadow-md">
        <div
          class="relative h-32 w-full cursor-crosshair touch-none rounded-md border border-black/10"
          :style="{ background: `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, ${hueHex})` }"
          @pointerdown="onSquare"
        >
          <span
            class="pointer-events-none absolute size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-[0_0_0_1px_rgba(0,0,0,0.3)]"
            :style="{ left: `${hsv[1] * 100}%`, top: `${(1 - hsv[2]) * 100}%`, background: modelValue }"
          />
        </div>
        <div
          class="relative mt-3 h-3 w-full cursor-pointer touch-none rounded-full border border-black/10"
          style="background: linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)"
          @pointerdown="onHue"
        >
          <span
            class="pointer-events-none absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-[0_0_0_1px_rgba(0,0,0,0.3)]"
            :style="{ left: `${(hsv[0] / 360) * 100}%`, background: hueHex }"
          />
        </div>
        <div class="mt-3 flex items-center gap-2">
          <span class="size-7 shrink-0 rounded-md border border-black/10" :style="{ background: modelValue }" />
          <input
            v-model="hexDraft"
            class="h-7 w-full rounded-md border border-input bg-card px-2 font-mono text-[12px] uppercase outline-none focus:border-ring"
            spellcheck="false"
            @blur="commitHex"
            @keydown.enter="commitHex"
          >
        </div>
        <div class="mt-3 grid grid-cols-10 gap-1">
          <button
            v-for="c in palette" :key="c"
            class="aspect-square rounded-[4px] border border-black/10 transition hover:scale-110"
            :class="{ 'ring-2 ring-ring ring-offset-1': c.toLowerCase() === modelValue.toLowerCase() }"
            :style="{ background: c }"
            @click="emit('update:modelValue', c)"
          />
        </div>
      </PopoverContent>
    </Popover>
  </div>
</template>
