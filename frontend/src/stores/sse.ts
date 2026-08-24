import { defineStore } from "pinia";
import { ref } from "vue";
import { SSEConnectionState } from "@/types";

export const useSSEStore = defineStore("sse", () => {
  const financeState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const techState = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED);
  const dashboardState = ref<SSEConnectionState>(
    SSEConnectionState.DISCONNECTED,
  );

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
    setFinanceState,
    setTechState,
    setDashboardState,
  };
});
