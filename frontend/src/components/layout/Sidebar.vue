<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { TrendingUp, Code, Activity, Settings, ChevronLeft, ChevronRight, LogOut } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import ThemeToggle from '@/components/common/ThemeToggle.vue'

const props = defineProps<{
  collapsed: boolean
  mobileVisible: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const route = useRoute()
const authStore = useAuthStore()

const navItems = [
  { path: '/finance', name: '财经', icon: TrendingUp },
  { path: '/tech', name: '科技', icon: Code },
  { path: '/dashboard', name: '仪表盘', icon: Activity },
  { path: '/settings', name: '设置', icon: Settings },
]

const currentPath = computed(() => route.path)

function handleLogout() {
  authStore.logout()
}

const userName = computed(() => authStore.user?.name || '用户')
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: props.collapsed, 'mobile-visible': props.mobileVisible }">
    <div class="sidebar-header">
      <div class="brand-logo">IB</div>
      <span v-if="!props.collapsed" class="brand-name">InstantBoard</span>
    </div>

    <nav class="sidebar-nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: currentPath === item.path || currentPath.startsWith(item.path + '/') }"
        @click="emit('close')"
      >
        <component :is="item.icon" class="nav-icon" :size="20" />
        <span v-if="!props.collapsed" class="nav-label">{{ item.name }}</span>
      </router-link>
    </nav>

    <div class="sidebar-footer">
      <ThemeToggle v-if="!props.collapsed" />
      <div class="user-section" v-if="!props.collapsed">
        <div class="user-info">
          <div class="user-avatar">{{ userName.charAt(0) }}</div>
          <span class="user-name">{{ userName }}</span>
        </div>
        <button class="logout-btn" @click="handleLogout" title="登出">
          <LogOut :size="16" />
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background-color: var(--sidebar-bg);
  color: var(--sidebar-text);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width var(--transition-normal);
  overflow: hidden;
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed-width);
}

.sidebar.mobile-visible {
  width: 220px;
  transform: translateX(0);
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  min-height: 56px;
}

.brand-logo {
  width: 32px;
  height: 32px;
  background-color: var(--accent);
  color: white;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}

.sidebar-nav {
  flex: 1;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--sidebar-text);
  text-decoration: none;
  transition: background-color var(--transition-fast), color var(--transition-fast);
  white-space: nowrap;
}

.nav-item:hover {
  background-color: rgba(255, 255, 255, 0.08);
}

.nav-item.active {
  background-color: var(--sidebar-active-bg);
  color: var(--sidebar-active-text);
}

.nav-icon {
  flex-shrink: 0;
}

.nav-label {
  font-size: 14px;
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.user-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background-color: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.user-name {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}

.logout-btn {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  color: var(--sidebar-text);
  display: flex;
  align-items: center;
  transition: background-color var(--transition-fast);
}

.logout-btn:hover {
  background-color: rgba(239, 68, 68, 0.15);
  color: #F87171;
}

@media (max-width: 767px) {
  .sidebar {
    transform: translateX(-100%);
    width: 220px;
  }
  .sidebar.mobile-visible {
    transform: translateX(0);
  }
  .sidebar.collapsed {
    transform: translateX(-100%);
  }
}
</style>
