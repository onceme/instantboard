import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

declare module "vue-router" {
  interface RouteMeta {
    // Public pages (login / SSO callback) don't render the business sidebar and header
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
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to, _from, next) => {
  const token = localStorage.getItem("access_token");
  if (to.name !== "login" && to.name !== "sso-callback" && !token) {
    next({ name: "login" });
  } else if (to.name === "login" && token) {
    next({ name: "finance" });
  } else if (to.meta.requiresAdmin) {
    // /dashboard requires the admin role; on page refresh the user may not be loaded yet, so fetch it once before checking
    const authStore = useAuthStore();
    if (token && !authStore.user) {
      await authStore.fetchCurrentUser();
    }
    if (authStore.isAdmin) {
      next();
    } else {
      next({ name: "finance" });
    }
  } else {
    next();
  }
});

export default router;
