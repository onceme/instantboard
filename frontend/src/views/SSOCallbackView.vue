<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuth } from "@/composables/useAuth";
import { getApiErrorMessage } from "@/utils/api";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";

const route = useRoute();
const router = useRouter();
const { handleCallback } = useAuth();

const error = ref<string | null>(null);

function backToLogin() {
  router.push("/login");
}

onMounted(async () => {
  const code = route.query.code as string | null;
  const state = route.query.state as string | null;
  const provider = (sessionStorage.getItem("oauth_provider") as string) || null;

  // Missing state/provider validation info: redirect back to the login page with an error flag and let it show the message
  if (!provider || !state) {
    router.replace({ name: "login", query: { error: "csrf_mismatch" } });
    return;
  }

  if (!code) {
    error.value = "授权码缺失，请重新登录。";
    return;
  }

  try {
    await handleCallback(provider, code, state);
  } catch (err) {
    // Login failed: extract a readable message from the backend error envelope and show it on the page instead of silently redirecting
    error.value = getApiErrorMessage(err, "登录失败，请重试。");
    console.error("SSO callback error:", err);
  }
});
</script>

<template>
  <div class="sso-callback">
    <div class="callback-card">
      <div v-if="error" class="callback-error">
        <p class="error-text">{{ error }}</p>
        <button class="back-btn" @click="backToLogin">返回登录</button>
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
  margin-bottom: 16px;
}

.back-btn {
  padding: 8px 24px;
  border-radius: var(--radius-md);
  font-size: 14px;
  color: white;
  background-color: var(--accent);
  transition: opacity var(--transition-fast);
}

.back-btn:hover {
  opacity: 0.9;
}
</style>
