import { onMounted, onUnmounted } from 'vue'
import { SSEConnection, SSEConnectionState } from '@/utils/sse.ts'
import type { SSEEventType } from '@/types'
import { useAuthStore } from '@/stores/auth'

type SSEEventHandler = (data: unknown) => void

export function useSSE(
  category: string,
  eventHandlers: Partial<Record<SSEEventType, SSEEventHandler>>
) {
  const authStore = useAuthStore()
  let connection: SSEConnection | null = null
  const state = ref<SSEConnectionState>(SSEConnectionState.DISCONNECTED)

  function connect() {
    connection = new SSEConnection({
      category,
      token: authStore.token,
      onStateChange: (newState) => {
        state.value = newState
      },
      eventHandlers,
    })
    connection.connect()
  }

  function disconnect() {
    if (connection) {
      connection.disconnect()
      connection = null
    }
  }

  onMounted(() => {
    if (authStore.isAuthenticated) {
      connect()
    }
  })

  onUnmounted(() => {
    disconnect()
  })

  return {
    state,
    connect,
    disconnect,
  }
}

import { ref } from 'vue'
