<script setup lang="ts">
import { ref, computed } from "vue";
import type { Category } from "@/types";
import { apiGet, apiPost, apiPut, apiDelete } from "@/utils/api";
import { Plus, Pencil, Trash2 } from "lucide-vue-next";
import EmptyState from "@/components/common/EmptyState.vue";

const categories = ref<Category[]>([]);
const loading = ref(false);
const editingId = ref<string | null>(null);
const newName = ref("");
const newDescription = ref("");

const customCategories = computed(() =>
  categories.value.filter((c) => c.type === "custom"),
);
const predefinedCategories = computed(() =>
  categories.value.filter((c) => c.type !== "custom"),
);

async function fetchCategories() {
  loading.value = true;
  try {
    const response = await apiGet<Category[]>("/categories");
    categories.value = response.data;
  } finally {
    loading.value = false;
  }
}

async function addCategory() {
  if (!newName.value.trim()) return;
  await apiPost<Category>("/categories", {
    name: newName.value,
    description: newDescription.value,
    type: "custom",
  });
  newName.value = "";
  newDescription.value = "";
  await fetchCategories();
}

async function updateCategory(id: string) {
  if (!newName.value.trim()) return;
  await apiPut<Category>(`/categories/${id}`, {
    name: newName.value,
    description: newDescription.value,
  });
  editingId.value = null;
  newName.value = "";
  newDescription.value = "";
  await fetchCategories();
}

async function deleteCategory(id: string) {
  await apiDelete(`/categories/${id}`);
  await fetchCategories();
}

function startEdit(category: Category) {
  editingId.value = category.id;
  newName.value = category.name;
  newDescription.value = category.description || "";
}

function cancelEdit() {
  editingId.value = null;
  newName.value = "";
  newDescription.value = "";
}

fetchCategories();
</script>

<template>
  <div class="category-editor">
    <div class="add-section">
      <input
        v-model="newName"
        type="text"
        placeholder="新分类名称"
        class="input-name"
      />
      <input
        v-model="newDescription"
        type="text"
        placeholder="描述(可选)"
        class="input-desc"
      />
      <button class="add-btn" :disabled="!newName.trim()" @click="addCategory">
        <Plus :size="16" />
        添加
      </button>
    </div>

    <div class="category-list">
      <h4 class="list-label">预定义分类</h4>
      <div
        v-for="cat in predefinedCategories"
        :key="cat.id"
        class="category-item predefined"
      >
        <span class="cat-name">{{ cat.name }}</span>
        <span class="cat-type">{{ cat.type }}</span>
        <span class="cat-slug">{{ cat.slug }}</span>
      </div>

      <h4 class="list-label">自定义分类</h4>
      <EmptyState
        v-if="customCategories.length === 0"
        title="暂无自定义分类"
        description="点击上方添加按钮创建"
        icon="folder"
      />
      <div
        v-for="cat in customCategories"
        :key="cat.id"
        class="category-item custom"
      >
        <div v-if="editingId === cat.id" class="edit-row">
          <input v-model="newName" type="text" class="input-name" />
          <input v-model="newDescription" type="text" class="input-desc" />
          <button class="save-btn" @click="updateCategory(cat.id)">保存</button>
          <button class="cancel-btn" @click="cancelEdit">取消</button>
        </div>
        <div v-else class="display-row">
          <span class="cat-name">{{ cat.name }}</span>
          <span class="cat-desc">{{ cat.description }}</span>
          <button class="edit-btn" @click="startEdit(cat)">
            <Pencil :size="14" />
          </button>
          <button class="delete-btn" @click="deleteCategory(cat.id)">
            <Trash2 :size="14" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.category-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.add-section {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

/* min-width: 0 overrides the replaced-element min-content size that would
   otherwise keep the inputs wider than the row on narrow screens (360-414px);
   the flex-basis decides when the fields wrap onto their own rows instead of
   pushing the 添加 button half off-screen */
.input-name {
  flex: 1 1 140px;
  min-width: 0;
}

.input-desc {
  flex: 2 1 200px;
  min-width: 0;
}

.add-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--accent);
  border: 1px solid var(--accent);
  background-color: transparent;
  transition: all var(--transition-fast);
}

.add-btn:hover:not(:disabled) {
  background-color: var(--accent);
  color: white;
}

.add-btn:disabled {
  opacity: 0.5;
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.list-label {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: 600;
  margin-top: 4px;
}

.category-item {
  padding: 8px 12px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.category-item.predefined {
  opacity: 0.6;
}

.display-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cat-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.cat-desc {
  color: var(--text-secondary);
  font-size: 13px;
  flex: 1;
}

.cat-type {
  font-size: 12px;
  color: var(--text-muted);
}

.cat-slug {
  font-size: 12px;
  color: var(--text-muted);
}

.edit-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.save-btn {
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--success);
  border: 1px solid var(--success);
  background-color: transparent;
}

.cancel-btn {
  flex-shrink: 0;
  padding: 4px 12px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-muted);
  border: 1px solid var(--border-color);
  background-color: transparent;
}

.edit-btn,
.delete-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: all var(--transition-fast);
}

.edit-btn:hover {
  color: var(--accent);
  background-color: rgba(59, 130, 246, 0.1);
}

.delete-btn:hover {
  color: var(--danger);
  background-color: rgba(239, 68, 68, 0.1);
}
</style>
