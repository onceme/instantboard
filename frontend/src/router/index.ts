import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore, readStoredSessionEntry } from "@/stores/auth";

declare module "vue-router" {
  interface RouteMeta {
    // Public pages (login / SSO callback / admin login) don't render the business sidebar and header
    public?: boolean;
    requiresAdmin?: boolean;
  }
}

const routes = [
  {
    path: "/",
    redirect: "/finance",
  },
  {
    path: "/finance",
    name: "finance",
    component: () => import("@/views/FinanceView.vue"),
  },
  {
    path: "/tech",
    name: "tech",
    component: () => import("@/views/TechView.vue"),
  },
  {
    // Generic feed view for custom categories; plain login guard like /
    path: "/c/:slug",
    name: "category",
    component: () => import("@/views/CategoryView.vue"),
  },
  {
    path: "/dashboard",
    name: "dashboard",
    component: () => import("@/views/DashboardView.vue"),
    meta: { requiresAdmin: true },
  },
  {
    path: "/settings",
    name: "settings",
    component: () => import("@/views/SettingsView.vue"),
  },
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    // The login page doesn't render the business sidebar/header
    meta: { public: true },
  },
  {
    path: "/auth/callback",
    name: "sso-callback",
    component: () => import("@/views/SSOCallbackView.vue"),
    // The SSO callback page doesn't render the business sidebar/header
    meta: { public: true },
  },
  {
    path: "/ibadmin",
    name: "admin-login",
    component: () => import("@/views/AdminLoginView.vue"),
    // The admin login page doesn't render the business sidebar/header
    meta: { public: true },
  },
];

// Back-office routes reachable in an admin-entry session
const ADMIN_ENTRY_ROUTES = new Set(["dashboard", "settings"]);

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to, _from, next) => {
  const authStore = useAuthStore();
  const token = localStorage.getItem("access_token");
  const entry = readStoredSessionEntry();

  // Admin login page: public; an active admin session goes straight to the dashboard,
  // while an SSO session is allowed to open it (logging in switches to an admin session)
  if (to.name === "admin-login") {
    if (token && entry === "admin") {
      next({ name: "dashboard" });
    } else {
      next();
    }
    return;
  }

  // SSO login / callback pages: public; an authenticated session is sent to its entry home
  if (to.name === "login" || to.name === "sso-callback") {
    if (to.name === "login" && token) {
      next(entry === "admin" ? { name: "dashboard" } : { name: "finance" });
    } else {
      next();
    }
    return;
  }

  // Everything below requires an authenticated session
  if (!token) {
    next({ name: "login" });
    return;
  }

  // Admin session: back-office routes only; front-end routes bounce back to the dashboard
  if (entry === "admin") {
    if (!ADMIN_ENTRY_ROUTES.has(String(to.name))) {
      next({ name: "dashboard" });
      return;
    }
    // On page refresh the user may not be loaded yet, so fetch it once before checking the role
    if (!authStore.user) {
      await authStore.fetchCurrentUser();
    }
    if (authStore.isAdmin) {
      next();
    } else {
      // Token no longer maps to an admin (e.g. admin features revoked); drop it and go back to the admin login
      authStore.clearSession();
      next({ name: "admin-login" });
    }
    return;
  }

  // SSO session: /dashboard requires the admin role
  if (to.meta.requiresAdmin) {
    if (!authStore.user) {
      await authStore.fetchCurrentUser();
    }
    if (authStore.isAdmin) {
      next();
    } else {
      next({ name: "finance" });
    }
    return;
  }

  next();
});

export default router;
