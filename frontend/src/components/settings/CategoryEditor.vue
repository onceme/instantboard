<script setup lang="ts">
import { ref, computed } from "vue";
import axios from "axios";
import type { Category } from "@/types";
import {
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  getApiErrorMessage,
} from "@/utils/api";
import { Plus, Pencil, Trash2 } from "lucide-vue-next";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorAlert from "@/components/common/ErrorAlert.vue";

const categories = ref<Category[]>([]);
const loading = ref(false);

// The top "add" form and the inline row editor keep separate state so starting
// a row edit no longer pours its values into the add inputs (and vice versa)
const addForm = ref({ name: "", description: "" });
const addError = ref("");
const editingId = ref<string | null>(null);
const editForm = ref({ name: "", description: "" });
const editError = ref("");
const deleteError = ref("");
// Bumped on every new delete error so a dismissed ErrorAlert remounts and shows again
const deleteErrorKey = ref(0);

const customCategories = computed(() =>
  categories.value.filter((c) => c.type === "custom"),
);
const predefinedCategories = computed(() =>
  categories.value.filter((c) => c.type !== "custom"),
);

// Status-driven Chinese fallbacks (409 duplicate / 400 quota / 403 permission);
// the backend envelope message wins whenever getApiErrorMessage can extract one
function categoryFallback(err: unknown, action: string): string {
  const status = axios.isAxiosError(err) ? err.response?.status : undefined;
  switch (status) {
    case 409:
      return "分类名称已存在";
    case 403:
      return "仅管理员可管理分类";
    case 400:
      return `${action}失败：请检查输入或分类数量是否已达上限`;
    default:
      return `${action}失败，请重试`;
  }
}

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
  if (!addForm.value.name.trim()) return;
  addError.value = "";
  try {
    await apiPost<Category>("/categories", {
      name: addForm.value.name,
      description: addForm.value.description,
      type: "custom",
    });
    // Success: clear the inputs and refresh; failure keeps both via catch
    addForm.value = { name: "", description: "" };
    await fetchCategories();
  } catch (err) {
    addError.value = getApiErrorMessage(err, categoryFallback(err, "添加"));
  }
}

async function updateCategory(id: string) {
  if (!editForm.value.name.trim()) return;
  editError.value = "";
  try {
    await apiPut<Category>(`/categories/${id}`, {
      name: editForm.value.name,
      description: editForm.value.description,
    });
    editingId.value = null;
    editForm.value = { name: "", description: "" };
    await fetchCategories();
  } catch (err) {
    // Stay in edit mode with the values and the error visible for retry
    editError.value = getApiErrorMessage(err, categoryFallback(err, "保存"));
  }
}

async function deleteCategory(id: string) {
  deleteError.value = "";
  try {
    await apiDelete(`/categories/${id}`);
    await fetchCategories();
  } catch (err) {
    deleteError.value = getApiErrorMessage(err, categoryFallback(err, "删除"));
    deleteErrorKey.value += 1;
  }
}

function startEdit(category: Category) {
  editingId.value = category.id;
  editForm.value = {
    name: category.name,
    description: category.description || "",
  };
  editError.value = "";
}

function cancelEdit() {
  editingId.value = null;
  editForm.value = { name: "", description: "" };
  editError.value = "";
}

fetchCategories();
</script>

<template>
  <div class="category-editor">
    <div class="add-section">
      <input
        v-model="addForm.name"
        type="text"
        placeholder="新分类名称"
        class="input-name"
      />
      <input
        v-model="addForm.description"
        type="text"
        placeholder="描述(可选)"
        class="input-desc"
      />
      <button
        class="add-btn"
        :disabled="!addForm.name.trim()"
        @click="addCategory"
      >
        <Plus :size="16" />
        添加
      </button>
      <p v-if="addError" class="error-text">{{ addError }}</p>
    </div>

    <ErrorAlert
      v-if="deleteError"
      :key="deleteErrorKey"
      :message="deleteError"
    />

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
          <input v-model="editForm.name" type="text" class="input-name" />
          <input
            v-model="editForm.description"
            type="text"
            class="input-desc"
          />
          <button class="save-btn" @click="updateCategory(cat.id)">保存</button>
          <button class="cancel-btn" @click="cancelEdit">取消</button>
          <p v-if="editError" class="error-text">{{ editError }}</p>
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

/* flex-basis 100% pushes the message onto its own line inside the wrapped rows */
.error-text {
  flex-basis: 100%;
  margin: 0;
  font-size: 13px;
  color: var(--danger);
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
