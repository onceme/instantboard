<script setup lang="ts">
import { computed } from "vue";
import { useTheme } from "@/composables/useTheme";
import { Sun, Moon, Monitor } from "lucide-vue-next";

const { theme, themeMode, toggleTheme } = useTheme();

// Button icon reflects the current mode: Sun=亮色, Moon=暗色, Monitor=跟随系统.
// Clicking cycles 亮色 → 暗色 → 跟随系统 (kept in sync with ProfileSettings).
const toggleTitle = computed(() => {
  switch (themeMode.value) {
    case "light":
      return "当前主题：亮色（点击切换为暗色）";
    case "dark":
      return "当前主题：暗色（点击切换为跟随系统）";
    default:
      return `当前主题：跟随系统（${theme.value === "dark" ? "暗色" : "亮色"}，点击切换为亮色）`;
  }
});
</script>

<template>
  <button
    class="theme-toggle"
    :title="toggleTitle"
    :aria-label="toggleTitle"
    @click="toggleTheme"
  >
    <Sun v-if="themeMode === 'light'" :size="18" />
    <Moon v-else-if="themeMode === 'dark'" :size="18" />
    <Monitor v-else :size="18" />
  </button>
</template>

<style scoped>
.theme-toggle {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.theme-toggle:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}
</style>
