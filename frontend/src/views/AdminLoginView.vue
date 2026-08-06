<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { getApiErrorCode, getApiErrorMessage } from "@/utils/api";
import LoadingSpinner from "@/components/common/LoadingSpinner.vue";

const router = useRouter();
const authStore = useAuthStore();

const email = ref("");
const password = ref("");
const loading = ref(false);
const loginError = ref<string | null>(null);

// Backend error codes mapped to user-facing messages
const ERROR_MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "邮箱或密码错误",
  ADMIN_LOGIN_DISABLED: "管理员登录未启用",
};

async function handleSubmit() {
  if (loading.value) return;
  loginError.value = null;

  if (!email.value.trim() || !password.value) {
    loginError.value = "请输入邮箱和密码";
    return;
  }

  loading.value = true;
  try {
    await authStore.adminLogin(email.value.trim(), password.value);
    router.push("/dashboard");
  } catch (err) {
    // Prefer the per-code mapping; fall back to the backend message, then a generic one
    const code = getApiErrorCode(err);
    loginError.value =
      (code && ERROR_MESSAGES[code]) ||
      getApiErrorMessage(err, "登录失败，请重试。");
    console.error("Admin login error:", err);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="admin-login-view">
    <div class="admin-login-card">
      <div class="admin-login-header">
        <div class="admin-login-logo">IB</div>
        <h1 class="admin-login-title">InstantBoard</h1>
        <p class="admin-login-desc">管理员登录</p>
      </div>

      <form class="admin-login-form" @submit.prevent="handleSubmit">
        <div class="form-field">
          <label class="form-label" for="admin-email">邮箱</label>
          <input
            id="admin-email"
            v-model="email"
            type="email"
            class="form-input"
            placeholder="请输入邮箱"
            autocomplete="email"
            required
            :disabled="loading"
          />
        </div>

        <div class="form-field">
          <label class="form-label" for="admin-password">密码</label>
          <input
            id="admin-password"
            v-model="password"
            type="password"
            class="form-input"
            placeholder="请输入密码"
            autocomplete="current-password"
            required
            :disabled="loading"
          />
        </div>

        <button type="submit" class="submit-btn" :disabled="loading">
          <span class="submit-label">登录</span>
          <LoadingSpinner v-if="loading" />
        </button>
      </form>

      <p v-if="loginError" class="login-error">{{ loginError }}</p>
    </div>
  </div>
</template>

<style scoped>
.admin-login-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--bg-secondary);
}

.admin-login-card {
  width: 100%;
  max-width: 400px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  padding: 32px;
}

.admin-login-header {
  text-align: center;
  margin-bottom: 24px;
}

.admin-login-logo {
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

.admin-login-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.admin-login-desc {
  font-size: 14px;
  color: var(--text-muted);
}

.admin-login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  background-color: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
  transition: border-color var(--transition-fast);
}

.form-input:focus {
  border-color: var(--accent);
}

.form-input:disabled {
  opacity: 0.7;
}

.submit-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: white;
  background-color: var(--accent);
  transition: background-color var(--transition-fast);
}

.submit-btn:hover:not(:disabled) {
  background-color: var(--accent-hover);
}

.submit-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.login-error {
  margin-top: 16px;
  font-size: 13px;
  color: var(--color-error, #e53935);
  text-align: center;
}
</style>
