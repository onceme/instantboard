<script setup lang="ts">
import { ref } from 'vue'
import ProfileSettings from '@/components/settings/ProfileSettings.vue'
import CategoryEditor from '@/components/settings/CategoryEditor.vue'
import SourceEditor from '@/components/settings/SourceEditor.vue'

const activeTab = ref<'profile' | 'categories' | 'sources'>('profile')

const tabs = [
  { key: 'profile', label: 'Profile' },
  { key: 'categories', label: 'Categories' },
  { key: 'sources', label: 'Sources' },
]
</script>

<template>
  <div class="settings-view">
    <div class="settings-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key as any"
      >
        {{ tab.label }}
      </button>
    </div>

    <div class="settings-content">
      <ProfileSettings v-if="activeTab === 'profile'" />
      <CategoryEditor v-if="activeTab === 'categories'" />
      <SourceEditor v-if="activeTab === 'sources'" />
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-tabs {
  display: flex;
  gap: 8px;
}

.tab-btn {
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--accent);
  border-color: var(--accent);
}

.settings-content {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}
</style>
