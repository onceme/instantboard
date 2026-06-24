import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'

export function useAuth() {
  const authStore = useAuthStore()
  const router = useRouter()

  async function loginWithSSO(provider: string) {
    const redirectUri = `${window.location.origin}/auth/callback`
    window.location.href = `/api/v1/auth/sso/${provider}/authorize?redirect_uri=${encodeURIComponent(redirectUri)}`
  }

  async function handleCallback(provider: string, code: string) {
    const redirectUri = `${window.location.origin}/auth/callback`
    await authStore.login(provider, code, redirectUri)
    router.push('/finance')
  }

  async function logout() {
    await authStore.logout()
    router.push('/login')
  }

  function isAdmin(): boolean {
    return authStore.user?.role === 'admin'
  }

  function canAccessDashboard(): boolean {
    return authStore.isAuthenticated && isAdmin()
  }

  return {
    loginWithSSO,
    handleCallback,
    logout,
    isAdmin,
    canAccessDashboard,
  }
}
