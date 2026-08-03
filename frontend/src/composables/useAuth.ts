import { useAuthStore } from "@/stores/auth";
import { useRouter } from "vue-router";
import { apiGet } from "@/utils/api";

const OAUTH_STATE_KEY = "oauth_state";
const OAUTH_PROVIDER_KEY = "oauth_provider";

export function useAuth() {
  const authStore = useAuthStore();
  const router = useRouter();

  async function loginWithSSO(provider: string) {
    const redirectUri = `${window.location.origin}/auth/callback`;
    const response = await apiGet<{ authorize_url: string; state: string }>(
      `/auth/sso/${provider}/authorize`,
      { redirect_uri: redirectUri },
    );
    sessionStorage.setItem(OAUTH_STATE_KEY, response.data.state);
    sessionStorage.setItem(OAUTH_PROVIDER_KEY, provider);
    window.location.href = response.data.authorize_url;
  }

  async function handleCallback(provider: string, code: string, state: string) {
    const storedState = sessionStorage.getItem(OAUTH_STATE_KEY);
    if (!storedState || storedState !== state) {
      sessionStorage.removeItem(OAUTH_STATE_KEY);
      sessionStorage.removeItem(OAUTH_PROVIDER_KEY);
      router.push({ name: "login", query: { error: "csrf_mismatch" } });
      return;
    }
    sessionStorage.removeItem(OAUTH_STATE_KEY);
    sessionStorage.removeItem(OAUTH_PROVIDER_KEY);

    const redirectUri = `${window.location.origin}/auth/callback`;
    await authStore.login(provider, code, redirectUri);
    router.push("/finance");
  }

  async function logout() {
    await authStore.logout();
    router.push("/login");
  }

  function isAdmin(): boolean {
    return authStore.user?.role === "admin";
  }

  function canAccessDashboard(): boolean {
    return authStore.isAuthenticated && isAdmin();
  }

  return {
    loginWithSSO,
    handleCallback,
    logout,
    isAdmin,
    canAccessDashboard,
  };
}
