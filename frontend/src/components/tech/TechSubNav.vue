<script setup lang="ts">
import { useTechStore } from '@/stores/tech'
import { useResponsive } from '@/composables/useResponsive'
import { DOMAIN_CONFIG } from '@/types'
import type { TechDomain, TechSort } from '@/types'
import { ChevronDown } from 'lucide-vue-next'
import { ref, computed } from 'vue'

const techStore = useTechStore()
const { isMobile } = useResponsive()
const domainDropdownOpen = ref(false)
const sortDropdownOpen = ref(false)

const domainButtons = computed(() => [
  { key: 'all', label: 'All', color: '' },
  ...Object.entries(DOMAIN_CONFIG).map(([key, config]) => ({
    key,
    label: config.label,
    color: config.color,
  })),
])

const sortButtons = [
  { key: 'hot', label: 'Hot' },
  { key: 'time', label: 'Time' },
  { key: 'relevance', label: 'Relevance' },
]

function selectDomain(domain: TechDomain) {
  techStore.setDomain(domain)
  domainDropdownOpen.value = false
}

function selectSort(sort: TechSort) {
  techStore.setSort(sort)
  sortDropdownOpen.value = false
}
</script>

<template>
  <div class="tech-sub-nav">
    <div v-if="isMobile" class="sub-nav-mobile">
      <div class="mobile-selectors">
        <button class="dropdown-trigger" @click="domainDropdownOpen = !domainDropdownOpen">
          <span class="domain-indicator" :style="{ backgroundColor: DOMAIN_CONFIG[techStore.currentDomain]?.color || 'var(--accent)' }" />
          <span>{{ domainButtons.find(d => d.key === techStore.currentDomain)?.label }}</span>
          <ChevronDown :size="16" />
        </button>
        <button class="dropdown-trigger" @click="sortDropdownOpen = !sortDropdownOpen">
          <span>{{ sortButtons.find(s => s.key === techStore.currentSort)?.label }}</span>
          <ChevronDown :size="16" />
        </button>
      </div>
      <Transition name="fade">
        <div v-if="domainDropdownOpen" class="dropdown-menu">
          <button
            v-for="item in domainButtons"
            :key="item.key"
            class="dropdown-item"
            :class="{ active: techStore.currentDomain === item.key }"
            @click="selectDomain(item.key as TechDomain)"
          >
            <span v-if="item.color" class="domain-dot" :style="{ backgroundColor: item.color }" />
            {{ item.label }}
          </button>
        </div>
      </Transition>
      <Transition name="fade">
        <div v-if="sortDropdownOpen" class="dropdown-menu">
          <button
            v-for="item in sortButtons"
            :key="item.key"
            class="dropdown-item"
            :class="{ active: techStore.currentSort === item.key }"
            @click="selectSort(item.key as TechSort)"
          >
            {{ item.label }}
          </button>
        </div>
      </Transition>
    </div>

    <div v-else class="sub-nav-desktop">
      <div class="domain-group">
        <button
          v-for="item in domainButtons"
          :key="item.key"
          class="domain-btn"
          :class="{ active: techStore.currentDomain === item.key }"
          @click="selectDomain(item.key as TechDomain)"
        >
          <span v-if="item.color" class="domain-indicator" :style="{ backgroundColor: item.color }" />
          {{ item.label }}
        </button>
      </div>

      <div class="sort-group">
        <button
          v-for="item in sortButtons"
          :key="item.key"
          class="sort-btn"
          :class="{ active: techStore.currentSort === item.key }"
          @click="selectSort(item.key as TechSort)"
        >
          {{ item.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tech-sub-nav {
  margin-bottom: 16px;
}

.sub-nav-desktop {
  display: flex;
  align-items: center;
  gap: 16px;
}

.domain-group {
  display: flex;
  gap: 6px;
}

.domain-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.domain-btn:hover {
  color: var(--text-primary);
  background-color: var(--bg-hover);
}

.domain-btn.active {
  color: var(--text-primary);
  border-color: var(--accent);
}

.domain-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.sort-group {
  display: flex;
  gap: 6px;
}

.sort-btn {
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  background-color: var(--bg-secondary);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.sort-btn:hover {
  color: var(--text-primary);
}

.sort-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  background-color: var(--bg-card);
}

.sub-nav-mobile {
  position: relative;
}

.mobile-selectors {
  display: flex;
  gap: 8px;
}

.dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
}

.dropdown-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 50;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 16px;
  font-size: 14px;
  color: var(--text-secondary);
  transition: all var(--transition-fast);
}

.dropdown-item:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.dropdown-item.active {
  color: var(--accent);
}

.domain-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
</style>
