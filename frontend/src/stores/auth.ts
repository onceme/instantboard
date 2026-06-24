import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { User, AuthTokens, UserPreferences } from '@/types'
import { apiPost, apiGet, apiDelete } from '@/utils/api'
import { SSEConnection, SSEConnectionState } from '@/utils/sse'
import { DEFAULT_THEME, DEFAULT_COLOR_SCHEME, SSO_PROVIDERS } from '@/utils/constants'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string>(localStorage.getItem('access_token') || '')
  const refreshToken = ref<string>(localStorage.getItem('refresh_token') || '')
  const isAuthenticated = computed(() => !!token.value && !!user.value)
  const tenantId = computed(() => user.value?.tenant_id || '')

  const colorScheme = ref<'chinese' | 'international'>(
    (localStorage.getItem('color_scheme') as 'chinese' | 'international') || DEFAULT_COLOR_SCHEME
  )
  const theme = ref<'light' | 'dark'>(
    (localStorage.getItem('theme') as 'light' | 'dark') || DEFAULT_THEME
  )

  function setTokens(tokens: AuthTokens) {
    token.value = tokens.access_token
    refreshToken.value = tokens.refresh_token
    localStorage.setItem('access_token', tokens.access_token)
    localStorage.setItem('refresh_token', tokens.refresh_token)
  }

  function setColorScheme(scheme: 'chinese' | 'international') {
    colorScheme.value = scheme
    localStorage.setItem('color_scheme', scheme)
    document.documentElement.setAttribute('data-color-scheme', scheme)
  }

  function setTheme(newTheme: 'light' | 'dark') {
    theme.value = newTheme
    localStorage.setItem('theme', newTheme)
    document.documentElement.setAttribute('data-theme', newTheme)
  }

  function initTheme() {
    const stored = localStorage.getItem('theme')
    if (stored) {
      setTheme(stored as 'light' | 'dark')
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      setTheme('dark')
    } else {
      setTheme(DEFAULT_THEME)
    }

    const storedScheme = localStorage.getItem('color_scheme')
    if (storedScheme) {
      setColorScheme(storedScheme as 'chinese' | 'international')
    } else {
      setColorScheme(DEFAULT_COLOR_SCHEME)
    }
  }

  async function login(provider: string, code: string, redirectUri: string) {
    const response = await apiPost<AuthTokens & { user: User }>('/auth/sso/' + provider, {
      code,
      redirect_uri: redirectUri,
    })
    setTokens({
      access_token: response.data.access_token,
      refresh_token: response.data.refresh_token,
      token_type: response.data.token_type,
      expires_in: response.data.expires_in,
    })
    user.value = response.data.user

    if (response.data.user.preferences) {
      const prefs = response.data.user.preferences
      if (prefs.color_scheme) setColorScheme(prefs.color_scheme)
      if (prefs.theme) setTheme(prefs.theme)
    }
  }

  async function fetchCurrentUser() {
    try {
      const response = await apiGet<User>('/auth/me')
      user.value = response.data
    } catch {
      user.value = null
    }
  }

  async function logout() {
    try {
      await apiDelete('/auth/logout')
    } finally {
      token.value = ''
      refreshToken.value = ''
      user.value = null
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
    }
  }

  function getSSOAuthorizeUrl(provider: string): string {
    const baseUrl = '/api/v1/auth/sso'
    return `${baseUrl}/${provider}/authorize`
  }

  return {
    user,
    token,
    refreshToken,
    isAuthenticated,
    tenantId,
    colorScheme,
    theme,
    setTokens,
    setColorScheme,
    setTheme,
    initTheme,
    login,
    fetchCurrentUser,
    logout,
    getSSOAuthorizeUrl,
  }
})
