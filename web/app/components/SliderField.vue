<script setup lang="ts">
import { Slider } from '@/components/ui/slider'

const props = withDefaults(defineProps<{
  label: string
  modelValue: number
  min: number
  max: number
  step?: number
  unit?: string
  decimals?: number
  hint?: string
  /** Value the reset dot returns to. Shown only when the current value differs. */
  defaultValue?: number
}>(), { step: 0.01, unit: '', decimals: 2 })
const emit = defineEmits<{ 'update:modelValue': [number] }>()

const dragging = ref(false)
const editing = ref(false)
const draft = ref('')

const arr = computed({
  get: () => [props.modelValue],
  set: (v: number[] | undefined) => { if (v && v[0] !== undefined) set(v[0]) },
})
const changed = computed(() => props.defaultValue !== undefined && Math.abs(props.defaultValue - props.modelValue) > 1e-9)
const text = computed(() => props.modelValue.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: props.decimals }))

function set(v: number) {
  if (Number.isNaN(v)) return
  emit('update:modelValue', Math.min(props.max, Math.max(props.min, v)))
}
function startEdit() {
  draft.value = props.modelValue.toFixed(props.decimals).replace(/\.?0+$/, '')
  editing.value = true
  nextTick(() => (document.getElementById(inputId) as HTMLInputElement | null)?.select())
}
function commit() {
  editing.value = false
  set(parseFloat(draft.value.replace(',', '.')))
}
function onWheel(e: WheelEvent) {
  e.preventDefault()
  set(+(props.modelValue + (e.deltaY < 0 ? props.step : -props.step)).toFixed(6))
}
const inputId = `sf-${Math.random().toString(36).slice(2, 8)}`
</script>

<template>
  <div class="group/field flex flex-col gap-2" :title="hint">
    <div class="flex h-5 items-center justify-between">
      <span class="flex items-center gap-1.5 text-[13px] text-foreground/80">
        {{ label }}
        <button
          v-if="changed"
          class="size-1.5 rounded-full bg-foreground/35 transition hover:scale-150 hover:bg-foreground"
          title="Reset to default"
          @click="set(defaultValue!)"
        />
      </span>
      <span class="flex items-baseline gap-1 font-mono text-[12px] tabular-nums">
        <input
          v-if="editing"
          :id="inputId"
          v-model="draft"
          class="h-5 w-14 rounded bg-card px-1 text-right text-[12px] outline-none ring-1 ring-input"
          @blur="commit"
          @keydown.enter="commit"
          @keydown.esc="editing = false"
        >
        <button
          v-else
          class="h-5 rounded px-1 text-right text-muted-foreground transition hover:bg-[var(--row-hover)] hover:text-foreground"
          @click="startEdit"
          @wheel="onWheel"
        >{{ text }}</button>
        <span v-if="unit" class="text-[10.5px] text-muted-foreground">{{ unit }}</span>
      </span>
    </div>
    <Slider
      v-model="arr"
      :min="min" :max="max" :step="step"
      class="h-4 [&_[data-slot=slider-thumb]]:size-3.5 [&_[data-slot=slider-thumb]]:border-[1.5px] [&_[data-slot=slider-thumb]]:border-foreground/70 [&_[data-slot=slider-range]]:bg-foreground/75 [&_[data-slot=slider-thumb]]:transition-transform [&_[data-slot=slider-thumb]]:hover:scale-110 [&_[data-slot=slider-thumb]]:active:scale-95 [&_[data-slot=slider-track]]:h-1 [&_[data-slot=slider-track]]:bg-muted"
      :class="{ '[&_[data-slot=slider-thumb]]:ring-4': dragging }"
      @pointerdown="dragging = true"
      @pointerup="dragging = false"
      @pointercancel="dragging = false"
    />
  </div>
</template>
