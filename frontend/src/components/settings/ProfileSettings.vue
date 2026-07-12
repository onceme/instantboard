<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
import { useTheme } from "@/composables/useTheme";
import { computed } from "vue";
import { Sun, Moon, Palette, Wifi, WifiOff } from "lucide-vue-next";
import { SSEConnectionState } from "@/types";

const authStore = useAuthStore();
const { theme, colorScheme, toggleTheme, toggleColorScheme } = useTheme();

const userName = computed(() => authStore.user?.name || "未登录");
const userEmail = computed(() => authStore.user?.email || "");
const userRole = computed(() => authStore.user?.role || "");

const colorSchemeLabel = computed(() =>
  colorScheme.value === "chinese"
    ? "中国配色 (红涨绿跌)"
    : "国际配色 (绿涨红跌)",
);

const sseStatus = computed(() => {
  return SSEConnectionState.CONNECTED;
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

    <div class="setting-item">
      <div class="setting-header">
        <Sun v-if="theme === 'dark'" :size="18" />
        <Moon v-else :size="18" />
        <span class="setting-label">主题</span>
      </div>
      <span class="setting-value">{{
        theme === "dark" ? "暗色" : "亮色"
      }}</span>
      <button class="toggle-btn" @click="toggleTheme">切换</button>
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
      <span
        class="setting-value"
        :class="
          sseStatus === SSEConnectionState.CONNECTED
            ? 'sse-connected'
            : 'sse-disconnected'
        "
      >
        {{ sseStatus === SSEConnectionState.CONNECTED ? "已连接" : "未连接" }}
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
</style>
