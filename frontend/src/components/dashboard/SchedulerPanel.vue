<script setup lang="ts">
import { computed, ref } from "vue";
import { useDashboardStore } from "@/stores/dashboard";
import { formatRelativeTime } from "@/utils/format";
import EmptyState from "@/components/common/EmptyState.vue";
import { Activity } from "lucide-vue-next";
import type { SchedulerJobInfo } from "@/types";

// Worker heartbeat freshness threshold. Mirrors the backend contract
// (RedisKeys.WORKER_HEARTBEAT_TTL = 45s): the worker writes a heartbeat to
// Redis every 15s with a 45s TTL (= 3x the write interval), so a heartbeat
// older than 45s — or missing entirely (key expired) — means the worker is
// down. Same semantics as worker_heartbeat_is_fresh() in
// backend/app/services/dashboard.py.
const HEARTBEAT_STALE_SECONDS = 45;

const dashboardStore = useDashboardStore();

const scheduler = computed(() => dashboardStore.scheduler);

type Tab = "running" | "paused";
const activeTab = ref<Tab>("running");

const totalJobs = computed(() => scheduler.value?.total_jobs ?? 0);
const runningCount = computed(() => scheduler.value?.running_jobs_count ?? 0);
const pausedCount = computed(() => scheduler.value?.paused_jobs_count ?? 0);

const activeJobs = computed<SchedulerJobInfo[]>(() =>
  activeTab.value === "running"
    ? (scheduler.value?.running_jobs ?? [])
    : (scheduler.value?.paused_jobs ?? []),
);

// Prod mode: the counts come from the worker heartbeat but the per-job detail
// lists only exist inside the worker container, so they are legitimately
// empty here.
const countedButNotListed = computed(() => {
  if (!scheduler.value) return false;
  return activeTab.value === "running"
    ? scheduler.value.running_jobs.length === 0 && runningCount.value > 0
    : scheduler.value.paused_jobs.length === 0 && pausedCount.value > 0;
});

type HeartbeatState = "fresh" | "stale" | "missing";

const heartbeat = computed<{
  state: HeartbeatState;
  ageSeconds: number | null;
}>(() => {
  const ts = scheduler.value?.last_heartbeat;
  if (!ts) return { state: "missing", ageSeconds: null };
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return { state: "missing", ageSeconds: null };
  const ageSeconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  return {
    state: ageSeconds < HEARTBEAT_STALE_SECONDS ? "fresh" : "stale",
    ageSeconds,
  };
});

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`;
  return `${Math.floor(seconds / 3600)}小时`;
}

function formatInterval(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || seconds <= 0) return "--";
  if (seconds < 60) return `${seconds}秒`;
  if (seconds % 3600 === 0) return `${seconds / 3600}小时`;
  if (seconds % 60 === 0) return `${seconds / 60}分钟`;
  return `${seconds}秒`;
}

function isAdaptive(job: SchedulerJobInfo): boolean {
  return (
    job.adaptive_multiplier !== null &&
    job.adaptive_multiplier !== undefined &&
    job.adaptive_multiplier !== 1
  );
}

function formatNextRun(dateStr?: string | null): string {
  if (!dateStr) return "--";
  const diffMs = Date.parse(dateStr) - Date.now();
  if (Number.isNaN(diffMs)) return "--";
  if (diffMs <= 0) return formatRelativeTime(dateStr);
  const seconds = Math.ceil(diffMs / 1000);
  if (seconds < 60) return `${seconds}秒后`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟后`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时后`;
  return `${Math.floor(seconds / 86400)}天后`;
}

function statusColor(status: string): string {
  if (status === "active") return "var(--success)";
  if (status === "paused") return "var(--warning)";
  return "var(--danger)";
}

function switchTab(tab: Tab) {
  activeTab.value = tab;
}
</script>

<template>
  <div class="scheduler-panel">
    <h3 class="panel-title">调度器任务</h3>

    <div v-if="scheduler" class="summary-cards">
      <div class="summary-card total">
        <span class="summary-count">{{ totalJobs }}</span>
        <span class="summary-label">总任务</span>
      </div>
      <div class="summary-card running">
        <span class="summary-count">{{ runningCount }}</span>
        <span class="summary-label">运行中</span>
      </div>
      <div class="summary-card paused">
        <span class="summary-count">{{ pausedCount }}</span>
        <span class="summary-label">已暂停</span>
      </div>
    </div>

    <div
      v-if="scheduler"
      class="heartbeat"
      :class="`heartbeat-${heartbeat.state}`"
    >
      <template v-if="heartbeat.state === 'fresh'">
        <Activity :size="14" class="heartbeat-icon" />
        <span
          >Worker 心跳正常 · {{ formatElapsed(heartbeat.ageSeconds!) }}前</span
        >
      </template>
      <template v-else-if="heartbeat.state === 'stale'">
        <span class="heartbeat-warning">
          ⚠️ 疑似掉线 · 心跳 {{ formatElapsed(heartbeat.ageSeconds!) }}前
        </span>
      </template>
      <template v-else>
        <span class="heartbeat-warning">⚠️ 疑似掉线 · 未收到 Worker 心跳</span>
      </template>
    </div>

    <div v-if="scheduler && totalJobs === 0" class="empty-wrapper">
      <EmptyState
        title="暂无调度任务"
        description="启用数据源后，采集任务会自动注册到调度器"
        icon="inbox"
      />
    </div>

    <template v-if="scheduler && totalJobs > 0">
      <div class="tabs" role="tablist">
        <button
          type="button"
          class="tab"
          :class="{ active: activeTab === 'running' }"
          role="tab"
          :aria-selected="activeTab === 'running'"
          @click="switchTab('running')"
        >
          运行中 ({{ runningCount }})
        </button>
        <button
          type="button"
          class="tab"
          :class="{ active: activeTab === 'paused' }"
          role="tab"
          :aria-selected="activeTab === 'paused'"
          @click="switchTab('paused')"
        >
          已暂停 ({{ pausedCount }})
        </button>
      </div>

      <p v-if="countedButNotListed" class="prod-note">
        生产模式：任务详情位于 worker 容器，此处仅展示心跳上报的计数
      </p>

      <div v-else-if="activeJobs.length === 0" class="empty-wrapper">
        <EmptyState
          :title="
            activeTab === 'running' ? '暂无运行中的任务' : '暂无暂停的任务'
          "
          icon="inbox"
        />
      </div>

      <div v-else class="jobs-table">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>周期</th>
              <th>上次运行</th>
              <th>下次运行</th>
              <th>状态</th>
              <th>24h 成功/失败</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in activeJobs" :key="job.job_id" class="job-row">
              <td class="cell-name" :title="job.job_id">{{ job.name }}</td>
              <td class="cell-interval">
                <template v-if="isAdaptive(job)">
                  <span class="interval-original">
                    {{ formatInterval(job.original_interval) }}
                  </span>
                  <span class="interval-arrow">→</span>
                  <span class="interval-current">
                    {{ formatInterval(job.current_interval) }}
                  </span>
                  <span class="interval-multiplier">
                    ×{{ job.adaptive_multiplier }}
                  </span>
                </template>
                <template v-else>{{
                  formatInterval(job.current_interval)
                }}</template>
              </td>
              <td>
                {{ job.last_run ? formatRelativeTime(job.last_run) : "--" }}
              </td>
              <td>{{ formatNextRun(job.next_run) }}</td>
              <td>
                <span
                  class="status-badge"
                  :style="{
                    backgroundColor: statusColor(job.status),
                    color: 'white',
                  }"
                >
                  {{ job.status }}
                </span>
              </td>
              <td class="cell-counts">
                <span class="count-success">{{
                  job.success_count_24h ?? "--"
                }}</span>
                <span class="count-sep">/</span>
                <span
                  class="count-failure"
                  :class="{ 'has-failures': (job.failure_count_24h ?? 0) > 0 }"
                >
                  {{ job.failure_count_24h ?? "--" }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.scheduler-panel {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.summary-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  min-width: 80px;
}

.summary-card.total {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.summary-card.running {
  background-color: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.summary-card.paused {
  background-color: rgba(245, 158, 11, 0.1);
  color: var(--warning);
}

.summary-count {
  font-size: 24px;
  font-weight: 700;
}

.summary-label {
  font-size: 12px;
  margin-top: 2px;
}

.heartbeat {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  margin-bottom: 12px;
}

.heartbeat-icon {
  color: var(--success);
}

.heartbeat-fresh {
  color: var(--success);
}

.heartbeat-stale,
.heartbeat-missing {
  color: var(--warning);
}

.heartbeat-warning {
  font-weight: 500;
}

.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.tab {
  font-size: 13px;
  color: var(--text-secondary);
  background-color: transparent;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 4px 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tab:hover {
  background-color: var(--bg-hover);
}

.tab.active {
  color: var(--accent);
  border-color: var(--accent);
  background-color: var(--bg-hover);
  font-weight: 600;
}

.prod-note {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.empty-wrapper {
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
}

.jobs-table {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  font-size: 12px;
  color: var(--text-muted);
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid var(--border-color);
}

tbody td {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 8px;
  border-bottom: 1px solid var(--border-light);
}

tbody tr.job-row {
  transition: background-color var(--transition-fast);
}

tbody tr.job-row:hover {
  background-color: var(--bg-hover);
}

.cell-name {
  font-weight: 500;
  color: var(--text-primary);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-interval {
  white-space: nowrap;
}

.interval-original {
  color: var(--text-muted);
  text-decoration: line-through;
}

.interval-arrow {
  margin: 0 4px;
  color: var(--text-muted);
}

.interval-current {
  color: var(--warning);
  font-weight: 500;
}

.interval-multiplier {
  margin-left: 4px;
  font-size: 11px;
  color: var(--warning);
  background-color: rgba(245, 158, 11, 0.1);
  border-radius: var(--radius-sm);
  padding: 1px 5px;
}

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}

.cell-counts {
  white-space: nowrap;
}

.count-success {
  color: var(--success);
}

.count-sep {
  margin: 0 4px;
  color: var(--text-muted);
}

.count-failure {
  color: var(--text-secondary);
}

.count-failure.has-failures {
  color: var(--danger);
  font-weight: 500;
}
</style>
