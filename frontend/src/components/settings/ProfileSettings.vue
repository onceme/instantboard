<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
import { useSSEStore } from "@/stores/sse";
import { useTheme, THEME_MODE_OPTIONS } from "@/composables/useTheme";
import { computed } from "vue";
import { useRoute } from "vue-router";
import { Sun, Moon, Monitor, Palette, Wifi, WifiOff } from "lucide-vue-next";
import { SSEConnectionState } from "@/types";

const authStore = useAuthStore();
const sseStore = useSSEStore();
const route = useRoute();
const { theme, themeMode, setThemeMode, colorScheme, toggleColorScheme } =
  useTheme();

const userName = computed(() => authStore.user?.name || "未登录");
const userEmail = computed(() => authStore.user?.email || "");
const userRole = computed(() => authStore.user?.role || "");

const colorSchemeLabel = computed(() =>
  colorScheme.value === "chinese"
    ? "中国配色 (红涨绿跌)"
    : "国际配色 (绿涨红跌)",
);

// Real aggregated SSE state: the finance/tech/dashboard stores feed the global
// SSE store while their views keep a stream open. On pages without a stream
// (e.g. /settings) this is honestly DISCONNECTED instead of a hardcoded value.
// Typed as the full enum so the defensive CONNECTING branches below stay valid
// even though overallState currently never yields it.
const sseStatus = computed<SSEConnectionState>(() => sseStore.overallState);

// Routes whose views keep a live SSE stream open while mounted
const SSE_ROUTES = ["/finance", "/tech", "/dashboard"];
const onSseRoute = computed(() =>
  SSE_ROUTES.some((prefix) => route.path.startsWith(prefix)),
);

// DISCONNECTED while not on an SSE route is expected (no view maintains a
// stream here): report it neutrally instead of a scary "未连接". A real stream
// that dropped keeps the warning wording.
const noStreamExpected = computed(
  () =>
    sseStatus.value === SSEConnectionState.DISCONNECTED && !onSseRoute.value,
);

const sseLabel = computed(() => {
  if (noStreamExpected.value) {
    return "当前页面无数据流（在 财经/科技/仪表盘 页面自动连接）";
  }
  switch (sseStatus.value) {
    case SSEConnectionState.CONNECTED:
      return "已连接";
    case SSEConnectionState.RECONNECTING:
    case SSEConnectionState.CONNECTING:
      return "连接中";
    default:
      return "未连接";
  }
});

const sseColorClass = computed(() => {
  if (noStreamExpected.value) return "sse-idle";
  switch (sseStatus.value) {
    case SSEConnectionState.CONNECTED:
      return "sse-connected";
    case SSEConnectionState.RECONNECTING:
    case SSEConnectionState.CONNECTING:
      return "sse-reconnecting";
    default:
      return "sse-disconnected";
  }
});
</script>

<template>
  <div class="profile-settings">
    <div class="user-section">
      <div class="user-avatar">
        {{ userName.charAt(0) }}
      </div>
      <div class="user-info">
        <span class="user-name">{{ userName }}</span>
        <span class="user-email">{{ userEmail }}</span>
        <span class="user-role">{{ userRole }}</span>
      </div>
    </div>

    <div class="setting-item theme-item">
      <div class="setting-header">
        <Sun v-if="themeMode === 'light'" :size="18" />
        <Moon v-else-if="themeMode === 'dark'" :size="18" />
        <Monitor v-else :size="18" />
        <span class="setting-label">主题</span>
      </div>
      <span v-if="themeMode === 'system'" class="setting-value">
        当前：{{ theme === "dark" ? "暗色" : "亮色" }}
      </span>
      <div class="theme-options" role="radiogroup" aria-label="主题选择">
        <button
          v-for="option in THEME_MODE_OPTIONS"
          :key="option.value"
          type="button"
          class="theme-option"
          :class="{ active: themeMode === option.value }"
          role="radio"
          :aria-checked="themeMode === option.value"
          @click="setThemeMode(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <div class="setting-item">
      <div class="setting-header">
        <Palette :size="18" />
        <span class="setting-label">涨跌配色</span>
      </div>
      <span class="setting-value">{{ colorSchemeLabel }}</span>
      <button class="toggle-btn" @click="toggleColorScheme">切换</button>
    </div>

    <div class="setting-item">
      <div class="setting-header">
        <Wifi v-if="sseStatus === SSEConnectionState.CONNECTED" :size="18" />
        <WifiOff v-else :size="18" />
        <span class="setting-label">SSE连接</span>
      </div>
      <span class="setting-value" :class="sseColorClass">
        {{ sseLabel }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.profile-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.user-section {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
}

.user-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background-color: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 600;
}

.user-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.user-email {
  font-size: 13px;
  color: var(--text-secondary);
}

.user-role {
  font-size: 12px;
  color: var(--text-muted);
}

.setting-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

/* Allow the segmented theme selector to wrap on narrow screens instead of
   overflowing the card */
.theme-item {
  flex-wrap: wrap;
}

.setting-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.setting-label {
  font-size: 14px;
  color: var(--text-primary);
}

.setting-value {
  font-size: 13px;
  color: var(--text-secondary);
}

.toggle-btn {
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.toggle-btn:hover {
  background-color: var(--accent);
  color: white;
}

.theme-options {
  display: flex;
  gap: 4px;
  padding: 3px;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.theme-option {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-secondary);
  background-color: transparent;
  white-space: nowrap;
  transition: all var(--transition-fast);
}

.theme-option:hover {
  color: var(--text-primary);
}

.theme-option.active {
  color: var(--accent);
  background-color: var(--bg-card);
  box-shadow: var(--shadow-sm);
}

.sse-connected {
  color: var(--success);
}

.sse-reconnecting {
  color: var(--warning);
}

.sse-disconnected {
  color: var(--danger);
}

/* Neutral tone: no stream is expected on this page, so it's not an error */
.sse-idle {
  color: var(--text-muted);
}
</style>
