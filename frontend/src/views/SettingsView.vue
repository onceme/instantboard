<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import ProfileSettings from "@/components/settings/ProfileSettings.vue";
import CategoryEditor from "@/components/settings/CategoryEditor.vue";
import SourceEditor from "@/components/settings/SourceEditor.vue";
import TenantOverrides from "@/components/settings/TenantOverrides.vue";

type SettingsTab = "profile" | "categories" | "sources" | "tenant-overrides";

interface SettingsTabDef {
  key: SettingsTab;
  label: string;
}

// Personal sections, shown to SSO-entry sessions only
const PERSONAL_TABS: SettingsTabDef[] = [{ key: "profile", label: "个人设置" }];

// Back-office sections, shown to admin-entry sessions only
const ADMIN_TABS: SettingsTabDef[] = [
  { key: "sources", label: "数据源管理" },
  { key: "categories", label: "分类管理" },
];

const authStore = useAuthStore();

// The session entry decides which sections are visible; a missing/unknown entry
// already falls back to "sso" inside the store (readStoredSessionEntry),
// matching the router guard
const isAdminSession = computed(() => authStore.sessionEntry === "admin");

// The override panel is role-gated, not entry-gated: only role=admin can PUT
// /tenant/settings (members get 403), so the tab follows authStore.isAdmin and
// is appended to whichever layout the session entry already shows
const TENANT_TAB: SettingsTabDef = {
  key: "tenant-overrides",
  label: "租户覆盖",
};

const tabs = computed<SettingsTabDef[]>(() => {
  const base = isAdminSession.value ? ADMIN_TABS : PERSONAL_TABS;
  return authStore.isAdmin ? [...base, TENANT_TAB] : base;
});

// Default landing tab: the first admin section for admin sessions, profile for
// SSO sessions. The entry only ever changes on login/logout, which always
// navigates away from this view, so a mount-time value is sufficient.
const activeTab = ref<SettingsTab>(tabs.value[0].key);
</script>

<template>
  <div class="settings-view">
    <div class="settings-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <div class="settings-content">
      <ProfileSettings v-if="activeTab === 'profile'" />
      <CategoryEditor v-if="activeTab === 'categories'" />
      <SourceEditor v-if="activeTab === 'sources'" />
      <TenantOverrides v-if="activeTab === 'tenant-overrides'" />
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-tabs {
  display: flex;
  gap: 8px;
}

.tab-btn {
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--accent);
  border-color: var(--accent);
}

.settings-content {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}
</style>
