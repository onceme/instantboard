<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuth } from "@/composables/useAuth";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";

const route = useRoute();
const router = useRouter();
const { handleCallback } = useAuth();

const error = ref<string | null>(null);

onMounted(async () => {
  const code = route.query.code as string | null;
  const state = route.query.state as string | null;
  const provider = (sessionStorage.getItem("oauth_provider") as string) || null;

  if (!code) {
    error.value = "授权码缺失，请重新登录。";
    setTimeout(() => router.push("/login"), 2000);
    return;
  }

  if (!provider) {
    error.value = "登录提供商信息丢失，请重新登录。";
    setTimeout(() => router.push("/login"), 2000);
    return;
  }

  if (!state) {
    error.value = "安全验证参数缺失，请重新登录。";
    setTimeout(() => router.push("/login"), 2000);
    return;
  }

  try {
    await handleCallback(provider, code, state);
  } catch (err) {
    error.value = "登录失败，请重试。";
    console.error("SSO callback error:", err);
    setTimeout(() => router.push("/login"), 2000);
  }
});
</script>

<template>
  <div class="sso-callback">
    <div class="callback-card">
      <div v-if="error" class="callback-error">
        <p class="error-text">{{ error }}</p>
      </div>
      <div v-else class="callback-loading">
        <LoadingSpinner />
        <p class="loading-text">正在完成登录...</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sso-callback {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--bg-secondary);
}

.callback-card {
  width: 100%;
  max-width: 400px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  padding: 48px 32px;
  text-align: center;
}

.callback-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.loading-text {
  font-size: 16px;
  color: var(--text-muted);
}

.callback-error {
  color: var(--color-error, #e53935);
}

.error-text {
  font-size: 16px;
  margin-bottom: 8px;
}
</style>
