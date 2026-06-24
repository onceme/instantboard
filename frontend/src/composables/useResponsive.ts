import { ref, onMounted, onUnmounted } from 'vue'
import { BREAKPOINTS } from '@/utils/constants'

export type BreakpointName = 'xs' | 'sm' | 'md' | 'lg' | 'xl'

export function useResponsive() {
  const currentBreakpoint = ref<BreakpointName>('lg')
  const isMobile = ref(false)
  const isTablet = ref(false)
  const isDesktop = ref(true)
  const showRightPanel = ref(true)
  const sidebarCollapsed = ref(false)

  function updateBreakpoint() {
    const width = window.innerWidth

    if (width < BREAKPOINTS.sm) {
      currentBreakpoint.value = 'xs'
      isMobile.value = true
      isTablet.value = false
      isDesktop.value = false
      showRightPanel.value = false
      sidebarCollapsed.value = true
    } else if (width < BREAKPOINTS.md) {
      currentBreakpoint.value = 'sm'
      isMobile.value = false
      isTablet.value = true
      isDesktop.value = false
      showRightPanel.value = false
      sidebarCollapsed.value = true
    } else if (width < BREAKPOINTS.lg) {
      currentBreakpoint.value = 'md'
      isMobile.value = false
      isTablet.value = false
      isDesktop.value = true
      showRightPanel.value = false
      sidebarCollapsed.value = false
    } else if (width < BREAKPOINTS.xl) {
      currentBreakpoint.value = 'lg'
      isMobile.value = false
      isTablet.value = false
      isDesktop.value = true
      showRightPanel.value = true
      sidebarCollapsed.value = false
    } else {
      currentBreakpoint.value = 'xl'
      isMobile.value = false
      isTablet.value = false
      isDesktop.value = true
      showRightPanel.value = true
      sidebarCollapsed.value = false
    }
  }

  onMounted(() => {
    updateBreakpoint()
    window.addEventListener('resize', updateBreakpoint)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', updateBreakpoint)
  })

  return {
    currentBreakpoint,
    isMobile,
    isTablet,
    isDesktop,
    showRightPanel,
    sidebarCollapsed,
  }
}
