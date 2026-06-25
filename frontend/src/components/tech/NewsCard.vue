<script setup lang="ts">
import type { TechNewsItem } from '@/types'
import { DOMAIN_CONFIG } from '@/types'
import { formatRelativeTime } from '@/utils/format'
import { computed } from 'vue'
import TopicTag from './TopicTag.vue'
import { ExternalLink } from 'lucide-vue-next'

const props = defineProps<{
  item: TechNewsItem
}>()

const primaryDomain = computed(() => {
  for (const tag of props.item.topic_tags) {
    if (DOMAIN_CONFIG[tag]) return tag
  }
  return props.item.topic_tags[0] || ''
})

const domainColorVar = computed(() => {
  if (primaryDomain.value && DOMAIN_CONFIG[primaryDomain.value]) {
    return `var(--domain-${primaryDomain.value})`
  }
  return 'var(--accent)'
})

const visibleTags = computed(() => props.item.topic_tags.slice(0, 3))
</script>

<template>
  <div
    class="news-card"
    :style="{ '--domain-color': domainColorVar }"
  >
    <div class="domain-stripe" />

    <div class="card-content">
      <div class="card-header">
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="card-title text-truncate"
        >
          {{ item.title }}
        </a>
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="ext-link"
        >
          <ExternalLink :size="14" />
        </a>
      </div>

      <p
        v-if="item.summary"
        class="card-summary line-clamp-2"
      >
        {{ item.summary }}
      </p>

      <div class="card-tags">
        <TopicTag
          v-for="tag in visibleTags"
          :key="tag"
          :tag="tag"
          :domain="primaryDomain"
        />
      </div>

      <div class="card-footer">
        <span class="card-source">{{ item.source_name }}</span>
        <span class="card-time">{{ formatRelativeTime(item.published_at) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.news-card {
  display: flex;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: box-shadow var(--transition-normal);
}

.news-card:hover {
  box-shadow: var(--shadow-sm);
}

.domain-stripe {
  width: 4px;
  background-color: var(--domain-color);
  flex-shrink: 0;
}

.card-content {
  flex: 1;
  padding: 12px;
  min-width: 0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  text-decoration: none;
  flex: 1;
  min-width: 0;
  line-height: 1.4;
}

.card-title:hover {
  color: var(--accent);
}

.ext-link {
  color: var(--text-muted);
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.ext-link:hover {
  color: var(--accent);
}

.card-summary {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 6px;
  line-height: 1.5;
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.card-source {
  font-size: 12px;
  color: var(--text-muted);
}

.card-time {
  font-size: 12px;
  color: var(--text-muted);
}

@media (max-width: 767px) {
  .card-summary {
    display: none;
  }

  .card-tags {
    margin-top: 4px;
  }

  .ext-link {
    display: none;
  }
}
</style>
