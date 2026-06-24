<script setup lang="ts">
import { computed } from 'vue'
import { Inbox, Star, Link, Newspaper, FolderOpen } from 'lucide-vue-next'

const props = defineProps<{
  title: string
  description?: string
  icon?: string
}>()

const emit = defineEmits<{
  action: []
}>()

const iconComponent = computed(() => {
  const iconMap: Record<string, typeof Inbox> = {
    inbox: Inbox,
    star: Star,
    link: Link,
    news: Newspaper,
    folder: FolderOpen,
  }
  return iconMap[props.icon || 'inbox'] || Inbox
})
</script>

<template>
  <div class="empty-state">
    <component :is="iconComponent" :size="48" class="empty-icon" />
    <h3 class="empty-title">{{ title }}</h3>
    <p v-if="description" class="empty-desc">{{ description }}</p>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
}

.empty-icon {
  color: var(--text-muted);
  margin-bottom: 12px;
}

.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.empty-desc {
  font-size: 14px;
  color: var(--text-muted);
}
</style>
