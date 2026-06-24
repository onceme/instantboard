<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Source, Category } from '@/types'
import { apiGet, apiPost, apiPut, apiDelete } from '@/utils/api'
import { Plus, ToggleLeft, ToggleRight } from 'lucide-vue-next'
import EmptyState from '@/components/common/EmptyState.vue'

const sources = ref<Source[]>([])
const categories = ref<Category[]>([])
const loading = ref(false)
const showAddForm = ref(false)
const newSource = ref({
  name: '',
  category_id: '',
  source_type: 'rss' as 'rss' | 'api' | 'web_scrape' | 'social',
  url: '',
  refresh_interval_seconds: 300,
})

async function fetchSources() {
  loading.value = true
  try {
    const response = await apiGet<Source[]>('/sources')
    sources.value = response.data
  } finally {
    loading.value = false
  }
}

async function fetchCategories() {
  const response = await apiGet<Category[]>('/categories')
  categories.value = response.data
}

async function addSource() {
  if (!newSource.value.name.trim() || !newSource.value.url.trim()) return
  await apiPost<Source>('/sources', newSource.value)
  showAddForm.value = false
  newSource.value = {
    name: '',
    category_id: '',
    source_type: 'rss',
    url: '',
    refresh_interval_seconds: 300,
  }
  await fetchSources()
}

async function toggleSource(source: Source) {
  await apiPut<Source>(`/sources/${source.id}`, {
    is_active: !source.is_active,
  })
  await fetchSources()
}

async function deleteSource(id: string) {
  await apiDelete(`/sources/${id}`)
  await fetchSources()
}

function healthStatusClass(status: string): string {
  if (status === 'healthy') return 'status-healthy'
  if (status === 'degraded') return 'status-degraded'
  return 'status-down'
}

function categoryName(categoryId: string): string {
  const cat = categories.value.find(c => c.id === categoryId)
  return cat?.name || categoryId
}

fetchSources()
fetchCategories()
</script>

<template>
  <div class="source-editor">
    <div class="add-section">
      <button class="add-btn" @click="showAddForm = !showAddForm">
        <Plus :size="16" />
        添加数据源
      </button>
    </div>

    <div v-if="showAddForm" class="add-form card">
      <input v-model="newSource.name" type="text" placeholder="数据源名称" />
      <select v-model="newSource.category_id">
        <option value="">选择分类</option>
        <option v-for="cat in categories" :key="cat.id" :value="cat.id">{{ cat.name }}</option>
      </select>
      <select v-model="newSource.source_type">
        <option value="rss">RSS</option>
        <option value="api">API</option>
        <option value="web_scrape">Web抓取</option>
        <option value="social">社交媒体</option>
      </select>
      <input v-model="newSource.url" type="text" placeholder="URL" />
      <button class="submit-btn" @click="addSource" :disabled="!newSource.name.trim() || !newSource.url.trim()">
        确认添加
      </button>
    </div>

    <EmptyState
      v-if="sources.length === 0"
      title="暂无数据源"
      description="点击添加按钮创建数据源"
      icon="link"
    />

    <div v-else class="source-list">
      <div v-for="source in sources" :key="source.id" class="source-item">
        <div class="source-main">
          <span class="source-name">{{ source.name }}</span>
          <span class="source-type">{{ source.source_type }}</span>
          <span class="source-category">{{ categoryName(source.category_id) }}</span>
        </div>
        <div class="source-actions">
          <span :class="healthStatusClass(source.health_status)" class="health-badge">
            {{ source.health_status }}
          </span>
          <button class="toggle-btn" @click="toggleSource(source)">
            <ToggleRight v-if="source.is_active" :size="18" :style="{ color: 'var(--success)' }" />
            <ToggleLeft v-else :size="18" :style="{ color: 'var(--text-muted)' }" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.source-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.add-section {
  display: flex;
  justify-content: flex-end;
}

.add-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.add-btn:hover {
  background-color: var(--accent);
  color: white;
}

.add-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
}

.submit-btn {
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
  align-self: flex-end;
}

.submit-btn:hover:not(:disabled) {
  background-color: var(--accent);
  color: white;
}

.submit-btn:disabled {
  opacity: 0.5;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.source-main {
  display: flex;
  align-items: center;
  gap: 12px;
}

.source-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.source-type {
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
}

.source-category {
  font-size: 12px;
  color: var(--text-secondary);
}

.source-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.health-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

.status-healthy {
  color: var(--success);
  background-color: rgba(16, 185, 129, 0.1);
}

.status-degraded {
  color: var(--warning);
  background-color: rgba(245, 158, 11, 0.1);
}

.status-down {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}

.toggle-btn {
  display: flex;
  align-items: center;
}
</style>
