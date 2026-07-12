import { ref, onMounted, onUnmounted } from "vue";

export function useInfiniteScroll(
  callback: () => Promise<void>,
  threshold: number = 200,
) {
  const isLoading = ref(false);
  const containerRef = ref<HTMLElement | null>(null);

  async function handleScroll() {
    if (isLoading.value) return;
    if (!containerRef.value) return;

    const { scrollTop, scrollHeight, clientHeight } = containerRef.value;
    if (scrollHeight - scrollTop - clientHeight < threshold) {
      isLoading.value = true;
      try {
        await callback();
      } finally {
        isLoading.value = false;
      }
    }
  }

  onMounted(() => {
    if (containerRef.value) {
      containerRef.value.addEventListener("scroll", handleScroll);
    }
  });

  onUnmounted(() => {
    if (containerRef.value) {
      containerRef.value.removeEventListener("scroll", handleScroll);
    }
  });

  return {
    isLoading,
    containerRef,
  };
}
