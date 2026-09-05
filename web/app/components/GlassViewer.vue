<script setup lang="ts">
import { GlassStudio, type GlassParams, type ModelDimensions } from '~/utils/glassScene'
import type { TraceResult } from '~/utils/trace'

const props = defineProps<{ trace: TraceResult | null; params: GlassParams }>()
const emit = defineEmits<{ dimensions: [ModelDimensions | null] }>()

const canvas = ref<HTMLCanvasElement | null>(null)
let studio: GlassStudio | null = null
let ro: ResizeObserver | null = null

onMounted(() => {
  studio = new GlassStudio(canvas.value!)
  studio.setParams({ ...props.params })
  ro = new ResizeObserver(() => studio?.resize())
  ro.observe(canvas.value!)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  studio?.dispose()
  studio = null
})

watch(() => props.trace, t => {
  studio?.setTrace(t)
  emit('dimensions', studio?.getDimensions() ?? null)
})
watch(() => ({ ...props.params }), p => {
  studio?.setParams(p)
  emit('dimensions', studio?.getDimensions() ?? null)
}, { deep: true })

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

defineExpose({
  frame: () => studio?.frame(),
  setView: (v: 'angle' | 'front' | 'side' | 'top') => studio?.setView(v),
  exportGLB: async () => { if (studio) download(await studio.exportGLB(), 'glass-logo.glb') },
  exportSTL: () => { if (studio) download(studio.exportSTL(), 'glass-logo.stl') },
  screenshot: async () => { if (studio) download(await studio.screenshot(), 'glass-logo.png') },
})
</script>

<template>
  <canvas ref="canvas" class="viewer" />
</template>

<style scoped>
.viewer { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; outline: none; }
</style>
