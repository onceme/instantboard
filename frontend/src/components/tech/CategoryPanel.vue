<script setup lang="ts">
import { useTechStore } from '@/stores/tech'
import { DOMAIN_CONFIG, SUBCATEGORY_MAP } from '@/types'
import { computed, ref } from 'vue'
import NewsCard from './NewsCard.vue'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'

const props = defineProps<{
  domain: string
}>()

const techStore = useTechStore()
const expanded = ref(true)
const activeSubcategory = ref('')

const config = computed(() => DOMAIN_CONFIG[props.domain])
const subcategories = computed(() => SUBCATEGORY_MAP[props.domain] || [])

const domainNews = computed(() => {
  let items = techStore.newsItems.filter((item) => {
    return item.topic_tags.some((tag) => tag === props.domain || tag.startsWith(props.domain))
  })

  if (activeSubcategory.value) {
    items = items.filter((item) => item.topic_tags.includes(activeSubcategory.value))
  }

  return items.slice(0, expanded.value ? 10 : 5)
})

function toggleExpand() {
  expanded.value = !expanded.value
}

function selectSubcategory(slug: string) {
  activeSubcategory.value = activeSubcategory.value === slug ? '' : slug
  techStore.setSubcategory(slug)
}

const domainColorVar = computed(() => `var(--domain-${props.domain})`)
</script>

<template>
  <div class="category-panel">
    <div class="domain-bar" :style="{ backgroundColor: domainColorVar }" />

    <div class="panel-header">
      <span class="domain-icon">{{ config?.icon }}</span>
      <h3 class="domain-title">{{ config?.label }}</h3>
      <button class="expand-btn" @click="toggleExpand">
        <ChevronUp v-if="expanded" :size="16" />
        <ChevronDown v-else :size="16" />
      </button>
    </div>

    <div class="subcategory-tags">
      <button
        v-for="sub in subcategories"
        :key="sub.slug"
        class="sub-tag"
        :class="{ active: activeSubcategory === sub.slug }"
        :style="{ '--tag-color': domainColorVar }"
        @click="selectSubcategory(sub.slug)"
      >
        {{ sub.label }}
      </button>
    </div>

    <div class="news-list" v-if="domainNews.length > 0">
      <NewsCard v-for="item in domainNews" :key="item.id" :item="item" />
    </div>

    <div v-else class="no-news">
      <p class="no-news-text">暂无新闻</p>
    </div>

    <button v-if="domainNews.length > 0" class="more-btn" @click="techStore.setDomain(props.domain as any)">
      查看更多
    </button>
  </div>
</template>

<style scoped>
.category-panel {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
  overflow: hidden;
}

.domain-bar {
  width: 100%;
  height: 4px;
  margin-bottom: 12px;
  border-radius: 2px;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.domain-icon {
  font-size: 20px;
}

.domain-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.expand-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: background-color var(--transition-fast);
}

.expand-btn:hover {
  background-color: var(--bg-hover);
}

.subcategory-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.sub-tag {
  padding: 3px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
}

.sub-tag:hover {
  color: var(--text-primary);
}

.sub-tag.active {
  color: white;
  background-color: var(--tag-color);
  border-color: var(--tag-color);
}

.news-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.no-news {
  padding: 16px;
  text-align: center;
}

.no-news-text {
  color: var(--text-muted);
  font-size: 14px;
}

.more-btn {
  margin-top: 8px;
  width: 100%;
  padding: 6px;
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.more-btn:hover {
  background-color: var(--accent);
  color: white;
}
</style>
