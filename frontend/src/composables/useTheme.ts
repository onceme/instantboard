import { ref, watch } from "vue";
import { useAuthStore } from "@/stores/auth";

export function useTheme() {
  const authStore = useAuthStore();

  const theme = ref<"light" | "dark">(authStore.theme);
  const colorScheme = ref<"chinese" | "international">(authStore.colorScheme);

  function toggleTheme() {
    const newTheme = theme.value === "light" ? "dark" : "light";
    theme.value = newTheme;
    authStore.setTheme(newTheme);
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
    colorScheme.value = authStore.colorScheme;
  }

  watch(
    () => authStore.theme,
    (newTheme) => {
      theme.value = newTheme;
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
    colorScheme,
    toggleTheme,
    toggleColorScheme,
    initTheme,
  };
}
