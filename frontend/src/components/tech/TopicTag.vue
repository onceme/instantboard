<script setup lang="ts">
import { DOMAIN_CONFIG } from "@/types";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    tag: string;
    domain: string;
    // When set, renders a small × to remove the tag (emits "remove")
    removable?: boolean;
  }>(),
  { removable: false },
);

const emit = defineEmits<{
  click: [tag: string];
  remove: [tag: string];
}>();

const domainColorVar = computed(() => {
  if (DOMAIN_CONFIG[props.domain]) {
    return `var(--domain-${props.domain})`;
  }
  return "var(--accent)";
});
</script>

<template>
  <button
    class="topic-tag"
    :style="{ '--tag-color': domainColorVar }"
    @click="emit('click', props.tag)"
  >
    {{ tag }}
    <span
      v-if="removable"
      class="tag-remove"
      role="button"
      :aria-label="`移除标签 ${tag}`"
      @click.stop.prevent="emit('remove', props.tag)"
      >×</span
    >
  </button>
</template>

<style scoped>
.topic-tag {
  padding: 2px 8px;
  border-radius: var(--radius-md);
  font-size: 11px;
  font-weight: 500;
  color: var(--tag-color);
  background-color: var(--bg-secondary);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.topic-tag:hover {
  background-color: var(--tag-color);
  color: white;
  border-color: var(--tag-color);
}

.tag-remove {
  margin-left: 4px;
  opacity: 0.6;
  font-weight: 700;
}

.tag-remove:hover {
  opacity: 1;
}
</style>
