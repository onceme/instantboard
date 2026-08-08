import { ref, onMounted, onUnmounted } from "vue";
import { BREAKPOINTS } from "@/utils/constants";

export type BreakpointName = "xs" | "sm" | "md" | "lg" | "xl" | "xxl";

interface ResponsiveFlags {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  showRightPanel: boolean;
  sidebarCollapsed: boolean;
}

function resolveBreakpoint(width: number): BreakpointName {
  if (width < BREAKPOINTS.sm) return "xs";
  if (width < BREAKPOINTS.md) return "sm";
  if (width < BREAKPOINTS.lg) return "md";
  if (width < BREAKPOINTS.xl) return "lg";
  if (width < BREAKPOINTS.xxl) return "xl";
  return "xxl";
}

// Flags mirror the CSS media queries so JS-driven markup and CSS layout agree
function resolveFlags(breakpoint: BreakpointName): ResponsiveFlags {
  switch (breakpoint) {
    case "xs":
    case "sm":
      // <768px: mobile layout — off-canvas sidebar, single column
      return {
        isMobile: true,
        isTablet: false,
        isDesktop: false,
        showRightPanel: false,
        sidebarCollapsed: true,
      };
    case "md":
      // 768-1023px: tablet — collapsed icon sidebar, right panel hidden
      return {
        isMobile: false,
        isTablet: true,
        isDesktop: false,
        showRightPanel: false,
        sidebarCollapsed: true,
      };
    case "lg":
      // 1024-1439px: laptop — full sidebar, right panel hidden
      return {
        isMobile: false,
        isTablet: false,
        isDesktop: true,
        showRightPanel: false,
        sidebarCollapsed: false,
      };
    case "xl":
    case "xxl":
      // >=1440px: large screens — full sidebar plus right panel
      return {
        isMobile: false,
        isTablet: false,
        isDesktop: true,
        showRightPanel: true,
        sidebarCollapsed: false,
      };
  }
}

export function useResponsive() {
  // Resolve synchronously during setup so the first paint already matches
  // the viewport (falls back to desktop defaults when window is unavailable)
  const initialBreakpoint = resolveBreakpoint(
    typeof window === "undefined" ? BREAKPOINTS.xl : window.innerWidth,
  );
  const initialFlags = resolveFlags(initialBreakpoint);

  const currentBreakpoint = ref<BreakpointName>(initialBreakpoint);
  const isMobile = ref(initialFlags.isMobile);
  const isTablet = ref(initialFlags.isTablet);
  const isDesktop = ref(initialFlags.isDesktop);
  const showRightPanel = ref(initialFlags.showRightPanel);
  const sidebarCollapsed = ref(initialFlags.sidebarCollapsed);

  function updateBreakpoint() {
    if (typeof window === "undefined") return;
    const breakpoint = resolveBreakpoint(window.innerWidth);
    const flags = resolveFlags(breakpoint);
    currentBreakpoint.value = breakpoint;
    isMobile.value = flags.isMobile;
    isTablet.value = flags.isTablet;
    isDesktop.value = flags.isDesktop;
    showRightPanel.value = flags.showRightPanel;
    sidebarCollapsed.value = flags.sidebarCollapsed;
  }

  onMounted(() => {
    updateBreakpoint();
    window.addEventListener("resize", updateBreakpoint);
  });

  onUnmounted(() => {
    window.removeEventListener("resize", updateBreakpoint);
  });

  return {
    currentBreakpoint,
    isMobile,
    isTablet,
    isDesktop,
    showRightPanel,
    sidebarCollapsed,
  };
}
