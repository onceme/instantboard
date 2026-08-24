<script setup lang="ts">
import AppLayout from "@/components/layout/AppLayout.vue";
import { useAuthStore } from "@/stores/auth";
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";

const authStore = useAuthStore();
const route = useRoute();

// Public pages (login / SSO callback) skip the business layout (sidebar/header) to avoid misleading interactions
const isPublicPage = computed(() => Boolean(route.meta.public));

onMounted(() => {
  authStore.initTheme();
  if (authStore.isAuthenticated) {
    authStore.fetchCurrentUser();
  }
});
</script>

<template>
  <router-view v-if="isPublicPage" />
  <AppLayout v-else>
    <router-view />
  </AppLayout>
</template>

<style scoped></style>
