/**
 * useResponsive regression: unified breakpoints 640/768/1024/1440/1920 and the
 * synchronous first-frame computation (no flash of wrong layout on load).
 */
import { afterEach, describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import { useResponsive } from "@/composables/useResponsive";

interface FirstFrame {
  breakpoint: string;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  showRightPanel: boolean;
  sidebarCollapsed: boolean;
}

// Mount a host component at a given viewport width and capture what
// useResponsive resolved synchronously during setup (before onMounted),
// plus the returned refs (kept in an outer holder) for later assertions.
function setupAt(width: number) {
  const captured: { value: FirstFrame | null } = { value: null };
  const holder: { value: ReturnType<typeof useResponsive> | null } = {
    value: null,
  };

  const Host = defineComponent({
    setup() {
      const responsive = useResponsive();
      holder.value = responsive;
      captured.value = {
        breakpoint: responsive.currentBreakpoint.value,
        isMobile: responsive.isMobile.value,
        isTablet: responsive.isTablet.value,
        isDesktop: responsive.isDesktop.value,
        showRightPanel: responsive.showRightPanel.value,
        sidebarCollapsed: responsive.sidebarCollapsed.value,
      };
      return {};
    },
    render: () => h("div"),
  });

  setInnerWidth(width);
  const wrapper = mount(Host);
  return {
    captured: captured.value as FirstFrame,
    responsive: holder.value as ReturnType<typeof useResponsive>,
    wrapper,
  };
}

function setInnerWidth(width: number) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
}

afterEach(() => {
  setInnerWidth(1024);
});

// Lower bound of each band: 639→xs, 640→sm, 768→md, 1024→lg, 1440→xl, 1920→xxl
const BOUNDARY_CASES: Array<[number, string]> = [
  [639, "xs"],
  [640, "sm"],
  [767, "sm"],
  [768, "md"],
  [1023, "md"],
  [1024, "lg"],
  [1439, "lg"],
  [1440, "xl"],
  [1919, "xl"],
  [1920, "xxl"],
];

describe("breakpoint mapping", () => {
  it.each(BOUNDARY_CASES)(
    "resolves %i px to '%s' synchronously during setup",
    (width, expected) => {
      const { captured } = setupAt(width);
      expect(captured.breakpoint).toBe(expected);
    },
  );
});

describe("responsive flags mirror CSS layout", () => {
  it("xs (500px): mobile layout with off-canvas sidebar", () => {
    const { captured } = setupAt(500);
    expect(captured.isMobile).toBe(true);
    expect(captured.isTablet).toBe(false);
    expect(captured.isDesktop).toBe(false);
    expect(captured.showRightPanel).toBe(false);
    expect(captured.sidebarCollapsed).toBe(true);
  });

  it("md (800px): tablet with collapsed icon sidebar, no right panel", () => {
    const { captured } = setupAt(800);
    expect(captured.isMobile).toBe(false);
    expect(captured.isTablet).toBe(true);
    expect(captured.isDesktop).toBe(false);
    expect(captured.showRightPanel).toBe(false);
    expect(captured.sidebarCollapsed).toBe(true);
  });

  it("lg (1200px): desktop with full sidebar, no right panel", () => {
    const { captured } = setupAt(1200);
    expect(captured.isDesktop).toBe(true);
    expect(captured.showRightPanel).toBe(false);
    expect(captured.sidebarCollapsed).toBe(false);
  });

  it("xl (1600px) and xxl (2560px): right panel enabled", () => {
    expect(setupAt(1600).captured.showRightPanel).toBe(true);
    expect(setupAt(2560).captured.showRightPanel).toBe(true);
  });
});

describe("resize handling", () => {
  it("updates breakpoint and flags on window resize after mount", () => {
    const { responsive } = setupAt(1200); // lg
    setInnerWidth(1600);
    window.dispatchEvent(new Event("resize"));

    expect(responsive.currentBreakpoint.value).toBe("xl");
    expect(responsive.showRightPanel.value).toBe(true);
    expect(responsive.sidebarCollapsed.value).toBe(false);
  });

  it("stops reacting to resize after unmount (listener removed)", () => {
    const { responsive, wrapper } = setupAt(1600); // xl
    wrapper.unmount();

    setInnerWidth(500);
    window.dispatchEvent(new Event("resize"));

    // Refs must be unaffected: the resize listener was removed on unmount
    expect(responsive.currentBreakpoint.value).toBe("xl");
  });
});
