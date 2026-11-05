<script setup lang="ts">
import { Collapsible, CollapsibleTrigger } from '@/components/ui/collapsible'
import { AnimatePresence, motion } from 'motion-v'

const props = withDefaults(defineProps<{
  title: string
  icon?: string
  defaultOpen?: boolean
  resettable?: boolean
  count?: string | number
}>(), { defaultOpen: true, resettable: false })
const emit = defineEmits<{ reset: [] }>()
const open = ref(props.defaultOpen)
</script>

<template>
  <Collapsible v-model:open="open" class="group/section pt-6 first:pt-0">
    <CollapsibleTrigger as-child>
      <button class="flex h-6 w-full items-center gap-1.5 px-4 text-left">
        <span class="text-[12px] font-medium text-muted-foreground">{{ title }}</span>
        <span v-if="count !== undefined" class="text-[11.5px] text-muted-foreground/70">{{ count }}</span>
        <span class="flex-1" />
        <button
          v-if="resettable"
          class="flex size-5 items-center justify-center rounded text-muted-foreground/70 opacity-0 transition hover:bg-[var(--row-hover)] hover:text-foreground group-hover/section:opacity-100"
          title="Reset" @click.stop="emit('reset')"
        ><Icon name="reset" :size="12" /></button>
        <motion.span :animate="{ rotate: open ? 0 : -90 }" :transition="{ type: 'spring', stiffness: 400, damping: 30 }" class="flex text-muted-foreground/60 opacity-0 transition group-hover/section:opacity-100" :class="{ 'opacity-100': !open }">
          <Icon name="chevron" :size="13" />
        </motion.span>
      </button>
    </CollapsibleTrigger>
    <AnimatePresence :initial="false">
      <motion.div
        v-if="open" key="body"
        :initial="{ height: 0, opacity: 0 }" :animate="{ height: 'auto', opacity: 1 }" :exit="{ height: 0, opacity: 0 }"
        :transition="{ duration: 0.22, ease: [0.25, 0.1, 0.25, 1] }"
        class="overflow-hidden"
      >
        <div class="flex flex-col gap-3.5 px-4 pt-2.5">
          <slot />
        </div>
      </motion.div>
    </AnimatePresence>
  </Collapsible>
</template>
