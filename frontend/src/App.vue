<script setup lang="ts">
import AppLayout from '@/components/layout/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'
import { onMounted } from 'vue'

const authStore = useAuthStore()

onMounted(() => {
  authStore.initTheme()
  if (authStore.isAuthenticated) {
    authStore.fetchCurrentUser()
  }
})
</script>

<template>
  <AppLayout>
    <router-view />
  </AppLayout>
</template>

<style scoped>
</style>
