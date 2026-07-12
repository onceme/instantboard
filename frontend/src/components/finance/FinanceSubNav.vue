<script setup lang="ts">
import { useFinanceStore } from "@/stores/finance";
import { useResponsive } from "@/composables/useResponsive";
import { FINANCE_SUB_NAV_ITEMS } from "@/utils/constants";
import type { FinancePanel } from "@/types";
import { ChevronDown } from "lucide-vue-next";
import { ref } from "vue";

const financeStore = useFinanceStore();
const { isMobile } = useResponsive();
const dropdownOpen = ref(false);

function selectPanel(panel: FinancePanel) {
  financeStore.setCurrentPanel(panel);
  dropdownOpen.value = false;
}

function toggleDropdown() {
  dropdownOpen.value = !dropdownOpen.value;
}
</script>

<template>
  <div class="finance-sub-nav">
    <div v-if="isMobile" class="sub-nav-mobile">
      <button class="dropdown-trigger" @click="toggleDropdown">
        <span>{{
          FINANCE_SUB_NAV_ITEMS.find((i) => i.key === financeStore.currentPanel)
            ?.label
        }}</span>
        <ChevronDown :size="16" />
      </button>
      <Transition name="fade">
        <div v-if="dropdownOpen" class="dropdown-menu">
          <button
            v-for="item in FINANCE_SUB_NAV_ITEMS"
            :key="item.key"
            class="dropdown-item"
            :class="{ active: financeStore.currentPanel === item.key }"
            @click="selectPanel(item.key as FinancePanel)"
          >
            {{ item.label }}
          </button>
        </div>
      </Transition>
    </div>

    <div v-else class="sub-nav-desktop">
      <button
        v-for="item in FINANCE_SUB_NAV_ITEMS"
        :key="item.key"
        class="sub-nav-btn"
        :class="{ active: financeStore.currentPanel === item.key }"
        @click="selectPanel(item.key as FinancePanel)"
      >
        {{ item.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.finance-sub-nav {
  margin-bottom: 16px;
}

.sub-nav-desktop {
  display: flex;
  gap: 8px;
  overflow-x: auto;
}

.sub-nav-btn {
  padding: 8px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.sub-nav-btn:hover {
  color: var(--text-primary);
  background-color: var(--bg-hover);
}

.sub-nav-btn.active {
  color: var(--accent);
  background-color: var(--bg-card);
  border-color: var(--accent);
}

.sub-nav-mobile {
  position: relative;
}

.dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  width: 100%;
}

.dropdown-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 50;
  overflow: hidden;
}

.dropdown-item {
  display: block;
  width: 100%;
  padding: 10px 16px;
  font-size: 14px;
  color: var(--text-secondary);
  transition: all var(--transition-fast);
  text-align: left;
}

.dropdown-item:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.dropdown-item.active {
  color: var(--accent);
  background-color: var(--bg-hover);
}
</style>
