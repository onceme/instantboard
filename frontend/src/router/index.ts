import { createRouter, createWebHistory } from "vue-router";

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
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem("access_token");
  if (to.name !== "login" && !token) {
    next({ name: "login" });
  } else if (to.name === "login" && token) {
    next({ name: "finance" });
  } else {
    next();
  }
});

export default router;
