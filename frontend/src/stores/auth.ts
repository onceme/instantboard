import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { User, AuthTokens } from "@/types";
import { apiPost, apiGet, apiDelete } from "@/utils/api";
import { DEFAULT_THEME, DEFAULT_COLOR_SCHEME } from "@/utils/constants";

// Which login entry created the current session: "sso" (regular front-end user) or
// "admin" (local admin via /ibadmin). Identities are isolated per entry and never merged;
// there is a single session slot, so a later login overwrites the earlier one.
export type SessionEntry = "sso" | "admin";

const SESSION_ENTRY_KEY = "session_entry";

// Read the persisted login entry; missing/unknown values fall back to the SSO entry
export function readStoredSessionEntry(): SessionEntry {
  return localStorage.getItem(SESSION_ENTRY_KEY) === "admin" ? "admin" : "sso";
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const token = ref<string>(localStorage.getItem("access_token") || "");
  const refreshToken = ref<string>(localStorage.getItem("refresh_token") || "");
  // Login entry of the persisted session, restored on page load
  const sessionEntry = ref<SessionEntry>(readStoredSessionEntry());
  const isAuthenticated = computed(() => !!token.value && !!user.value);
  const tenantId = computed(() => user.value?.tenant_id || "");
  // Shared admin check used by both the router guard and useAuth
  const isAdmin = computed(() => user.value?.role === "admin");

  const colorScheme = ref<"chinese" | "international">(
    (localStorage.getItem("color_scheme") as "chinese" | "international") ||
      DEFAULT_COLOR_SCHEME,
  );
  const theme = ref<"light" | "dark">(
    (localStorage.getItem("theme") as "light" | "dark") || DEFAULT_THEME,
  );

  function setTokens(tokens: AuthTokens) {
    token.value = tokens.access_token;
    refreshToken.value = tokens.refresh_token;
    localStorage.setItem("access_token", tokens.access_token);
    localStorage.setItem("refresh_token", tokens.refresh_token);
  }

  function setSessionEntry(entry: SessionEntry) {
    sessionEntry.value = entry;
    localStorage.setItem(SESSION_ENTRY_KEY, entry);
  }

  // Clear the local session state (tokens + user + entry) without calling the backend
  function clearSession() {
    token.value = "";
    refreshToken.value = "";
    user.value = null;
    sessionEntry.value = "sso";
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem(SESSION_ENTRY_KEY);
  }

  function setColorScheme(scheme: "chinese" | "international") {
    colorScheme.value = scheme;
    localStorage.setItem("color_scheme", scheme);
    document.documentElement.setAttribute("data-color-scheme", scheme);
  }

  function setTheme(newTheme: "light" | "dark") {
    theme.value = newTheme;
    localStorage.setItem("theme", newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
  }

  function initTheme() {
    const stored = localStorage.getItem("theme");
    if (stored) {
      setTheme(stored as "light" | "dark");
    } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      setTheme("dark");
    } else {
      setTheme(DEFAULT_THEME);
    }

    const storedScheme = localStorage.getItem("color_scheme");
    if (storedScheme) {
      setColorScheme(storedScheme as "chinese" | "international");
    } else {
      setColorScheme(DEFAULT_COLOR_SCHEME);
    }
  }

  function applyUserPreferences(target: User) {
    if (target.preferences) {
      const prefs = target.preferences;
      if (prefs.color_scheme) setColorScheme(prefs.color_scheme);
      if (prefs.theme) setTheme(prefs.theme);
    }
  }

  function storeLoginResponse(data: AuthTokens & { user: User }) {
    setTokens({
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      token_type: data.token_type,
      expires_in: data.expires_in,
    });
    user.value = data.user;
    applyUserPreferences(data.user);
  }

  async function login(provider: string, code: string, redirectUri: string) {
    const response = await apiPost<AuthTokens & { user: User }>(
      "/auth/sso/" + provider,
      {
        code,
        redirect_uri: redirectUri,
      },
    );
    storeLoginResponse(response.data);
    // SSO login creates an SSO-entry session (overwrites any previous session)
    setSessionEntry("sso");
  }

  async function adminLogin(email: string, password: string) {
    const response = await apiPost<AuthTokens & { user: User }>(
      "/auth/admin/login",
      { email, password },
    );
    storeLoginResponse(response.data);
    // Local admin login creates an admin-entry session (overwrites any previous session)
    setSessionEntry("admin");
  }

  async function fetchCurrentUser() {
    try {
      const response = await apiGet<User>("/auth/me");
      user.value = response.data;
    } catch {
      user.value = null;
    }
  }

  // Logs out and returns the login route matching the entry of the ended session
  async function logout(): Promise<string> {
    const entry = sessionEntry.value;
    try {
      await apiDelete("/auth/logout");
    } finally {
      clearSession();
    }
    return entry === "admin" ? "/ibadmin" : "/login";
  }

  function getSSOAuthorizeUrl(provider: string): string {
    const baseUrl = "/api/v1/auth/sso";
    return `${baseUrl}/${provider}/authorize`;
  }

  return {
    user,
    token,
    refreshToken,
    sessionEntry,
    isAuthenticated,
    isAdmin,
    tenantId,
    colorScheme,
    theme,
    setTokens,
    setColorScheme,
    setTheme,
    initTheme,
    login,
    adminLogin,
    clearSession,
    fetchCurrentUser,
    logout,
    getSSOAuthorizeUrl,
  };
});
