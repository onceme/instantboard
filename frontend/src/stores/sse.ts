import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { SSEConnectionState } from "@/types";

export const useSSEStore = defineStore("sse", () => {
  const financeState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const techState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const dashboardState = ref<SSEConnectionState>(
    SSEConnectionState.DISCONNECTED,
  );

  const overallState = computed(() => {
    const states = [financeState.value, techState.value, dashboardState.value];
    if (states.every((s) => s === SSEConnectionState.CONNECTED)) {
      return SSEConnectionState.CONNECTED;
    }
    if (states.some((s) => s === SSEConnectionState.RECONNECTING)) {
      return SSEConnectionState.RECONNECTING;
    }
    if (states.every((s) => s === SSEConnectionState.DISCONNECTED)) {
      return SSEConnectionState.DISCONNECTED;
    }
    return SSEConnectionState.CONNECTING;
  });

  function setFinanceState(state: SSEConnectionState) {
    financeState.value = state;
  }

  function setTechState(state: SSEConnectionState) {
    techState.value = state;
  }

  function setDashboardState(state: SSEConnectionState) {
    dashboardState.value = state;
  }

  return {
    financeState,
    techState,
    dashboardState,
    overallState,
    setFinanceState,
    setTechState,
    setDashboardState,
  };
});
