<script setup lang="ts">
import { useResponsive } from '@/composables/useResponsive'
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { ref } from 'vue'

const { isMobile, sidebarCollapsed } = useResponsive()
const sidebarVisible = ref(false)

function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value
}

function closeSidebar() {
  sidebarVisible.value = false
}
</script>

<template>
  <div class="app-layout">
    <Sidebar
      :collapsed="sidebarCollapsed"
      :mobile-visible="sidebarVisible"
      @close="closeSidebar"
    />

    <div class="main-area">
      <Header @toggle-sidebar="toggleSidebar" />
      <main class="main-content">
        <slot />
      </main>
    </div>

    <Transition name="fade">
      <div v-if="isMobile && sidebarVisible" class="sidebar-overlay" @click="closeSidebar" />
    </Transition>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  background-color: var(--bg-primary);
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  transition: margin-left var(--transition-normal);
}

.main-content {
  flex: 1;
  padding: var(--content-padding);
  overflow-y: auto;
  background-color: var(--bg-secondary);
}

.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 99;
}

@media (min-width: 768px) {
  .sidebar-overlay {
    display: none;
  }
}
</style>
