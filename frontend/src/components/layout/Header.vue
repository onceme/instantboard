<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { Menu, Wifi, WifiOff } from "lucide-vue-next";
import { useAuthStore } from "@/stores/auth";
import { useFinanceStore } from "@/stores/finance";
import { useTechStore } from "@/stores/tech";
import { useDashboardStore } from "@/stores/dashboard";
import { SSEConnectionState } from "@/types";
import ThemeToggle from "@/components/common/ThemeToggle.vue";

const emit = defineEmits<{
  toggleSidebar: [];
}>();

const route = useRoute();
const authStore = useAuthStore();
const financeStore = useFinanceStore();
const techStore = useTechStore();
const dashboardStore = useDashboardStore();

const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    finance: "财经",
    tech: "科技",
    dashboard: "仪表盘",
    settings: "设置",
    login: "登录",
  };
  return titles[route.name as string] || "InstantBoard";
});

const sseState = computed(() => {
  if (route.path.startsWith("/finance")) return financeStore.sseState;
  if (route.path.startsWith("/tech")) return techStore.sseState;
  if (route.path.startsWith("/dashboard")) return dashboardStore.sseState;
  return SSEConnectionState.DISCONNECTED;
});

const sseColorClass = computed(() => {
  switch (sseState.value) {
    case SSEConnectionState.CONNECTED:
      return "sse-connected";
    case SSEConnectionState.RECONNECTING:
      return "sse-reconnecting";
    case SSEConnectionState.CONNECTING:
      return "sse-reconnecting";
    default:
      return "sse-disconnected";
  }
});

const userName = computed(() => authStore.user?.name || "用户");
</script>

<template>
  <header class="app-header">
    <button class="hamburger-btn" @click="emit('toggleSidebar')">
      <Menu :size="20" />
    </button>

    <h1 class="page-title">
      {{ pageTitle }}
    </h1>

    <div class="header-actions">
      <div class="sse-indicator" :class="sseColorClass">
        <Wifi v-if="sseState === SSEConnectionState.CONNECTED" :size="16" />
        <WifiOff v-else :size="16" />
      </div>

      <ThemeToggle />

      <div class="user-menu">
        <div class="user-avatar">
          {{ userName.charAt(0) }}
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  background-color: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
  z-index: 10;
}

.hamburger-btn {
  display: none;
  padding: 8px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  transition: background-color var(--transition-fast);
}

.hamburger-btn:hover {
  background-color: var(--bg-hover);
}

@media (max-width: 767px) {
  .hamburger-btn {
    display: flex;
  }
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.sse-indicator {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: color var(--transition-fast);
}

.sse-indicator.sse-connected {
  color: var(--success);
}

.sse-indicator.sse-reconnecting {
  color: var(--warning);
  animation: blink 1s infinite;
}

.sse-indicator.sse-disconnected {
  color: var(--danger);
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.user-menu {
  display: flex;
  align-items: center;
}

.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background-color: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

@media (max-width: 767px) {
  .page-title {
    font-size: 16px;
  }
}
</style>
