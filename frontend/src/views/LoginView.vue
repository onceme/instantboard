<script setup lang="ts">
import { useAuth } from '@/composables/useAuth'
import { ref } from 'vue'
import { SSO_PROVIDERS } from '@/utils/constants'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'

const { loginWithSSO } = useAuth()
const loadingProvider = ref<string | null>(null)

async function handleLogin(provider: string) {
  loadingProvider.value = provider
  try {
    loginWithSSO(provider)
  } finally {
    loadingProvider.value = null
  }
}

const providerIcons: Record<string, string> = {
  google: 'G',
  azure_ad: 'M',
  github: 'GH',
  apple: '',
  facebook: 'f',
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo">
          IB
        </div>
        <h1 class="login-title">
          InstantBoard
        </h1>
        <p class="login-desc">
          实时信息聚合面板
        </p>
      </div>

      <div class="sso-buttons">
        <button
          v-for="provider in SSO_PROVIDERS"
          :key="provider.slug"
          class="sso-btn"
          :style="{ '--btn-color': provider.brand_color }"
          :disabled="loadingProvider === provider.slug"
          @click="handleLogin(provider.slug)"
        >
          <span class="sso-icon">{{ providerIcons[provider.slug] }}</span>
          <span class="sso-label">使用 {{ provider.name }} 登录</span>
          <LoadingSpinner v-if="loadingProvider === provider.slug" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--bg-secondary);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  padding: 32px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
}

.login-logo {
  width: 48px;
  height: 48px;
  background-color: var(--accent);
  color: white;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 20px;
  margin: 0 auto 12px;
}

.login-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.login-desc {
  font-size: 14px;
  color: var(--text-muted);
}

.sso-buttons {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sso-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: white;
  background-color: var(--btn-color);
  transition: opacity var(--transition-fast);
  width: 100%;
  justify-content: center;
}

.sso-btn:hover {
  opacity: 0.9;
}

.sso-btn:disabled {
  opacity: 0.7;
}

.sso-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 16px;
  border-radius: 4px;
  flex-shrink: 0;
}

.sso-label {
  flex: 1;
  text-align: center;
}
</style>
