<script setup lang="ts">
import { useResponsive } from "@/composables/useResponsive";
import Sidebar from "./Sidebar.vue";
import Header from "./Header.vue";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

const { isMobile, sidebarCollapsed } = useResponsive();
const sidebarVisible = ref(false);

function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value;
}

function closeSidebar() {
  sidebarVisible.value = false;
}

// Leaving the mobile breakpoint closes the drawer so a stale open state
// can never leak into the desktop layout
watch(isMobile, (mobile) => {
  if (!mobile) sidebarVisible.value = false;
});

// Lock body scroll while the mobile drawer is open (restored on close/unmount)
watch(
  [sidebarVisible, isMobile],
  ([visible, mobile]) => {
    document.body.style.overflow = visible && mobile ? "hidden" : "";
  },
  { immediate: true },
);

// ESC closes the drawer (nav-item click and overlay click are handled in the template)
function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeSidebar();
}

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  document.body.style.overflow = "";
});
</script>

<template>
  <div class="app-layout">
    <Sidebar
      :collapsed="sidebarCollapsed"
      :mobile-visible="sidebarVisible"
      :aria-hidden="isMobile && !sidebarVisible"
      @close="closeSidebar"
    />

    <div
      class="main-area"
      :class="{ 'sidebar-collapsed': sidebarCollapsed && !isMobile }"
    >
      <Header @toggle-sidebar="toggleSidebar" />
      <main class="main-content">
        <slot />
      </main>
    </div>

    <Transition name="fade">
      <div
        v-if="isMobile && sidebarVisible"
        class="sidebar-overlay"
        @click="closeSidebar"
      />
    </Transition>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  background-color: var(--bg-primary);
}

.main-area {
  flex: 1;
  /* Flex items default to min-width: auto, which lets wide content (tables,
     charts, nowrap rows) grow .main-area past `100vw - sidebar` and create a
     page-level horizontal scrollbar. min-width: 0 keeps it inside the viewport
     (wide content scrolls inside its own container instead). */
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  /* Compensate the fixed sidebar so it never covers content.
     --sidebar-width adapts per viewport: 0 (<768px), 60px (768-1023px), 220px (>=1024px) */
  margin-left: var(--sidebar-width);
  transition: margin-left var(--transition-normal);
}

.main-area.sidebar-collapsed {
  margin-left: var(--sidebar-collapsed-width);
}

.main-content {
  flex: 1;
  width: 100%;
  /* Same min-width: auto guard as .main-area: as a flex child its content
     must never push the layout wider than the viewport */
  min-width: 0;
  /* Keep line lengths / grid spans readable on very wide screens (1920x1080,
     2560x1440 and above): cap and center instead of stretching edge to edge */
  max-width: 1600px;
  margin-left: auto;
  margin-right: auto;
  padding: var(--content-padding);
  /* overflow-y: auto makes overflow-x compute to auto as well, so oversized
     content scrolls locally here instead of breaking the page width */
  overflow-y: auto;
  background-color: var(--bg-secondary);
}

@media (max-width: 767px) {
  /* Sidebar is off-canvas on mobile, no compensation needed */
  .main-area {
    margin-left: 0;
  }
}

.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 99;
}

@media (min-width: 768px) {
  .sidebar-overlay {
    display: none;
  }
}
</style>
