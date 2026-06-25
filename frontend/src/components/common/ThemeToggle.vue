<script setup lang="ts">
import { useTheme } from '@/composables/useTheme'
import { Sun, Moon } from 'lucide-vue-next'

const { theme, toggleTheme } = useTheme()
</script>

<template>
  <button
    class="theme-toggle"
    :title="theme === 'dark' ? '切换亮色' : '切换暗色'"
    @click="toggleTheme"
  >
    <Sun
      v-if="theme === 'dark'"
      :size="18"
    />
    <Moon
      v-else
      :size="18"
    />
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
  transition: background-color var(--transition-fast), color var(--transition-fast);
}

.theme-toggle:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}
</style>
