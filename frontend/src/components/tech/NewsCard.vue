<script setup lang="ts">
import type { TechNewsItem } from "@/types";
import { DOMAIN_CONFIG } from "@/types";
import { itemsApi } from "@/api/items";
import { getApiErrorMessage } from "@/utils/api";
import { formatRelativeTime } from "@/utils/format";
import { computed, nextTick, ref, watch } from "vue";
import TopicTag from "./TopicTag.vue";
import { Check, ExternalLink, Plus, X } from "lucide-vue-next";

const props = defineProps<{
  item: TechNewsItem;
}>();

// Backend UserTagRule (services/item.py USER_TAG_PATTERN): lowercase letters,
// digits and hyphens, 1-32 chars. Checked client-side to avoid a round-trip.
const USER_TAG_PATTERN = /^[a-z0-9-]{1,32}$/;

const primaryDomain = computed(() => {
  for (const tag of props.item.topic_tags) {
    if (DOMAIN_CONFIG[tag]) return tag;
  }
  return props.item.topic_tags[0] || "";
});

const domainColorVar = computed(() => {
  if (primaryDomain.value && DOMAIN_CONFIG[primaryDomain.value]) {
    return `var(--domain-${primaryDomain.value})`;
  }
  return "var(--accent)";
});

const visibleTags = computed(() => props.item.topic_tags.slice(0, 3));

const imgFailed = ref(false);
watch(
  () => props.item.image_url,
  () => {
    imgFailed.value = false;
  },
);

function onImgError() {
  imgFailed.value = true;
}

const tagInputOpen = ref(false);
const tagInput = ref("");
const tagBusy = ref(false);
const tagError = ref("");
const tagInputRef = ref<HTMLInputElement | null>(null);

function openTagInput() {
  tagError.value = "";
  tagInput.value = "";
  tagInputOpen.value = true;
  nextTick(() => tagInputRef.value?.focus());
}

function closeTagInput() {
  tagInputOpen.value = false;
  tagInput.value = "";
}

// The item is a shared reactive record from the feed (tech store / category
// feed array); updating its tags in place keeps the client-side tag filters
// (NewsFeed, TopicFilter) consistent with what the card shows. Scoped
// exception to vue/no-mutating-props: the parent never overwrites this field.
function applyTags(tags: string[]) {
  // eslint-disable-next-line vue/no-mutating-props
  props.item.topic_tags = tags;
}

async function submitTag() {
  const tag = tagInput.value.trim();
  if (!USER_TAG_PATTERN.test(tag)) {
    tagError.value = "标签仅限小写字母、数字、连字符（1-32 位）";
    return;
  }
  tagBusy.value = true;
  try {
    const res = await itemsApi.addItemTag(props.item.id, tag);
    // Server list is authoritative: it dedupes and preserves hierarchy order
    applyTags(res.data.topic_tags);
    tagError.value = "";
    closeTagInput();
  } catch (err) {
    tagError.value = getApiErrorMessage(err, "添加标签失败");
  } finally {
    tagBusy.value = false;
  }
}

function onTagInputKeydown(event: KeyboardEvent) {
  if (event.key === "Enter") {
    event.preventDefault();
    void submitTag();
  } else if (event.key === "Escape") {
    closeTagInput();
  }
}

async function removeTag(tag: string) {
  // Optimistic removal with rollback on failure
  const previous = [...props.item.topic_tags];
  applyTags(previous.filter((t) => t !== tag));
  tagBusy.value = true;
  try {
    const res = await itemsApi.removeItemTag(props.item.id, tag);
    applyTags(res.data.topic_tags);
    tagError.value = "";
  } catch (err) {
    applyTags(previous);
    tagError.value = getApiErrorMessage(err, "移除标签失败");
  } finally {
    tagBusy.value = false;
  }
}
</script>

<template>
  <div class="news-card" :style="{ '--domain-color': domainColorVar }">
    <div class="domain-stripe" />

    <div class="card-content">
      <div class="card-header">
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="card-title text-truncate"
        >
          {{ item.title }}
        </a>
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="ext-link"
        >
          <ExternalLink :size="14" />
        </a>
      </div>

      <p v-if="item.summary" class="card-summary line-clamp-2">
        {{ item.summary }}
      </p>

      <div class="card-tags">
        <TopicTag
          v-for="tag in visibleTags"
          :key="tag"
          :tag="tag"
          :domain="primaryDomain"
          removable
          :disabled="tagBusy"
          @remove="removeTag"
        />

        <form
          v-if="tagInputOpen"
          class="tag-input-group"
          @submit.prevent="submitTag"
        >
          <input
            ref="tagInputRef"
            v-model="tagInput"
            class="tag-input"
            type="text"
            maxlength="32"
            placeholder="新标签"
            aria-label="新标签"
            :disabled="tagBusy"
            @keydown="onTagInputKeydown"
          />
          <button
            type="submit"
            class="tag-input-confirm"
            aria-label="确认添加标签"
            :disabled="tagBusy"
          >
            <Check :size="12" />
          </button>
          <button
            type="button"
            class="tag-input-cancel"
            aria-label="取消添加标签"
            @click="closeTagInput"
          >
            <X :size="12" />
          </button>
        </form>

        <button
          v-else
          class="tag-add"
          aria-label="添加标签"
          :disabled="tagBusy"
          @click="openTagInput"
        >
          <Plus :size="12" />
        </button>
      </div>

      <p v-if="tagError" class="tag-error">{{ tagError }}</p>

      <div class="card-footer">
        <span class="card-source">{{ item.source_name }}</span>
        <span class="card-time">{{
          formatRelativeTime(item.published_at)
        }}</span>
      </div>
    </div>

    <a
      v-if="item.image_url && !imgFailed"
      :href="item.url"
      target="_blank"
      rel="noopener noreferrer"
      class="card-thumb-link"
    >
      <img
        :src="item.image_url"
        :alt="item.title"
        class="card-thumb"
        loading="lazy"
        @error="onImgError"
      />
    </a>
  </div>
</template>

<style scoped>
.news-card {
  display: flex;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: box-shadow var(--transition-normal);
}

.news-card:hover {
  box-shadow: var(--shadow-sm);
}

.domain-stripe {
  width: 4px;
  background-color: var(--domain-color);
  flex-shrink: 0;
}

.card-content {
  flex: 1;
  padding: 12px;
  min-width: 0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  text-decoration: none;
  flex: 1;
  min-width: 0;
  line-height: 1.4;
}

.card-title:hover {
  color: var(--accent);
}

.ext-link {
  color: var(--text-muted);
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.ext-link:hover {
  color: var(--accent);
}

.card-summary {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 6px;
  line-height: 1.5;
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
}

.tag-add,
.tag-input-confirm,
.tag-input-cancel {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background-color: transparent;
  border: 1px dashed var(--border-color);
  transition: all var(--transition-fast);
}

.tag-add:hover,
.tag-input-confirm:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.tag-input-cancel:hover {
  color: var(--danger);
  border-color: var(--danger);
}

.tag-input-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.tag-input {
  width: 88px;
  height: 20px;
  padding: 0 6px;
  font-size: 11px;
  color: var(--text-primary);
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  outline: none;
}

.tag-input:focus {
  border-color: var(--accent);
}

.tag-error {
  margin-top: 4px;
  font-size: 11px;
  color: var(--danger);
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.card-source {
  font-size: 12px;
  color: var(--text-muted);
}

.card-time {
  font-size: 12px;
  color: var(--text-muted);
}

.card-thumb-link {
  display: flex;
  align-items: flex-start;
  flex-shrink: 0;
  padding: 12px 12px 12px 0;
}

.card-thumb {
  width: 96px;
  height: 72px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  background-color: var(--bg-secondary);
}

@media (max-width: 767px) {
  .card-summary {
    display: none;
  }

  .card-tags {
    margin-top: 4px;
  }

  .ext-link {
    display: none;
  }

  .card-thumb-link {
    padding: 8px 8px 8px 0;
  }

  .card-thumb {
    width: 64px;
    height: 48px;
  }
}
</style>
