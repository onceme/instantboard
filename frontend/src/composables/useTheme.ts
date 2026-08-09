import { ref, watch } from "vue";
import { useAuthStore, type ThemeMode } from "@/stores/auth";

// Cycle order for the quick toggle: 亮色 → 暗色 → 跟随系统 → 亮色
const THEME_CYCLE: ThemeMode[] = ["light", "dark", "system"];

// Explicit options for the Profile settings selector (same three modes)
export const THEME_MODE_OPTIONS: Array<{ value: ThemeMode; label: string }> = [
  { value: "light", label: "亮色" },
  { value: "dark", label: "暗色" },
  { value: "system", label: "跟随系统" },
];

export function useTheme() {
  const authStore = useAuthStore();

  // Resolved theme actually applied to the DOM ("light" | "dark")
  const theme = ref<"light" | "dark">(authStore.theme);
  // Persisted selection, may be "system"
  const themeMode = ref<ThemeMode>(authStore.themeMode);
  const colorScheme = ref<"chinese" | "international">(authStore.colorScheme);

  function toggleTheme() {
    const next =
      THEME_CYCLE[
        (THEME_CYCLE.indexOf(themeMode.value) + 1) % THEME_CYCLE.length
      ];
    authStore.setThemeMode(next);
  }

  function setThemeMode(mode: ThemeMode) {
    authStore.setThemeMode(mode);
  }

  function toggleColorScheme() {
    const newScheme =
      colorScheme.value === "chinese" ? "international" : "chinese";
    colorScheme.value = newScheme;
    authStore.setColorScheme(newScheme);
  }

  function initTheme() {
    authStore.initTheme();
    theme.value = authStore.theme;
    themeMode.value = authStore.themeMode;
    colorScheme.value = authStore.colorScheme;
  }

  watch(
    () => authStore.theme,
    (newTheme) => {
      theme.value = newTheme;
    },
  );

  watch(
    () => authStore.themeMode,
    (newMode) => {
      themeMode.value = newMode;
    },
  );

  watch(
    () => authStore.colorScheme,
    (newScheme) => {
      colorScheme.value = newScheme;
    },
  );

  return {
    theme,
    themeMode,
    colorScheme,
    toggleTheme,
    setThemeMode,
    toggleColorScheme,
    initTheme,
  };
}
