<script setup lang="ts">
import { DOMAIN_CONFIG } from "@/types";
import { computed } from "vue";

const props = defineProps<{
  tag: string;
  domain: string;
}>();

const emit = defineEmits<{
  click: [tag: string];
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
</style>
