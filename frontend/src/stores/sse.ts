import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { SSEConnectionState } from "@/types";

export const useSSEStore = defineStore("sse", () => {
  const financeState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const techState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const dashboardState = ref<SSEConnectionState>(
    SSEConnectionState.DISCONNECTED,
  );

  // Overall = "is any live push channel up right now?". Views only keep their own
  // channel connected while mounted, so requiring every channel to be connected
  // (the old logic) reported DISCONNECTED/CONNECTING almost all the time.
  const overallState = computed(() => {
    const states = [financeState.value, techState.value, dashboardState.value];
    if (states.some((s) => s === SSEConnectionState.CONNECTED)) {
      return SSEConnectionState.CONNECTED;
    }
    if (
      states.some(
        (s) =>
          s === SSEConnectionState.RECONNECTING ||
          s === SSEConnectionState.CONNECTING,
      )
    ) {
      return SSEConnectionState.RECONNECTING;
    }
    return SSEConnectionState.DISCONNECTED;
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
