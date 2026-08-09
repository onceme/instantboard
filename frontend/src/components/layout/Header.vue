<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Menu, Wifi, WifiOff, LogOut, Settings } from "lucide-vue-next";
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
const router = useRouter();
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

// Routes that actually maintain an SSE connection; on any other route
// (e.g. /settings) the indicator is hidden instead of faking a red "disconnected"
const SSE_ROUTES = ["/finance", "/tech", "/dashboard"];

const showSseIndicator = computed(() =>
  SSE_ROUTES.some((prefix) => route.path.startsWith(prefix)),
);

const sseState = computed(() => {
  if (route.path.startsWith("/finance")) return financeStore.sseState;
  if (route.path.startsWith("/tech")) return techStore.sseState;
  if (route.path.startsWith("/dashboard")) return dashboardStore.sseState;
  return SSEConnectionState.DISCONNECTED;
});

const sseLabel = computed(() => {
  switch (sseState.value) {
    case SSEConnectionState.CONNECTED:
      return "实时推送：已连接";
    case SSEConnectionState.RECONNECTING:
    case SSEConnectionState.CONNECTING:
      return "实时推送：重连中";
    default:
      return "实时推送：已断开";
  }
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
const userEmail = computed(() => authStore.user?.email || "");

// ── User dropdown menu ─────────────────────────────────────────
const userMenuOpen = ref(false);
const userMenuRef = ref<HTMLElement | null>(null);

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value;
}

function closeUserMenu() {
  userMenuOpen.value = false;
}

// Close on click outside the menu container and on Escape
function onDocumentClick(event: MouseEvent) {
  if (userMenuRef.value && !userMenuRef.value.contains(event.target as Node)) {
    closeUserMenu();
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeUserMenu();
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick);
  document.removeEventListener("keydown", onKeydown);
});

function goToProfileSettings() {
  closeUserMenu();
  router.push("/settings");
}

async function handleLogout() {
  closeUserMenu();
  // logout() returns the login route matching the ended session entry
  const target = await authStore.logout();
  router.push(target);
}
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
      <div
        v-if="showSseIndicator"
        class="sse-indicator"
        :class="sseColorClass"
        role="status"
        :title="sseLabel"
        :aria-label="sseLabel"
      >
        <Wifi
          v-if="sseState === SSEConnectionState.CONNECTED"
          :size="16"
          aria-hidden="true"
        />
        <WifiOff v-else :size="16" aria-hidden="true" />
      </div>

      <ThemeToggle />

      <div ref="userMenuRef" class="user-menu">
        <button
          class="user-avatar"
          type="button"
          :title="userName"
          :aria-label="`用户菜单：${userName}`"
          :aria-expanded="userMenuOpen"
          aria-haspopup="menu"
          @click="toggleUserMenu"
        >
          {{ userName.charAt(0) }}
        </button>

        <div v-if="userMenuOpen" class="user-dropdown" role="menu">
          <div class="dropdown-user">
            <div class="dropdown-avatar">
              {{ userName.charAt(0) }}
            </div>
            <div class="dropdown-info">
              <span class="dropdown-name">{{ userName }}</span>
              <span v-if="userEmail" class="dropdown-email">{{
                userEmail
              }}</span>
            </div>
          </div>
          <button
            class="dropdown-item"
            role="menuitem"
            @click="goToProfileSettings"
          >
            <Settings :size="16" aria-hidden="true" />
            个人设置
          </button>
          <button
            class="dropdown-item danger"
            role="menuitem"
            @click="handleLogout"
          >
            <LogOut :size="16" aria-hidden="true" />
            退出登录
          </button>
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
  position: relative;
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
  transition: opacity var(--transition-fast);
}

.user-avatar:hover {
  opacity: 0.85;
}

.user-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 224px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: 50;
}

.dropdown-user {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--border-light);
}

.dropdown-avatar {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 50%;
  background-color: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
}

.dropdown-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.dropdown-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dropdown-email {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-primary);
  text-align: left;
  transition: background-color var(--transition-fast);
}

.dropdown-item:hover {
  background-color: var(--bg-hover);
}

.dropdown-item.danger {
  color: var(--danger);
}

.dropdown-item.danger:hover {
  background-color: rgba(239, 68, 68, 0.1);
}

@media (max-width: 767px) {
  .page-title {
    font-size: 16px;
  }

  /* Never let the dropdown overflow very narrow viewports */
  .user-dropdown {
    width: min(224px, calc(100vw - 32px));
  }
}
</style>
