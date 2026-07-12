<script setup lang="ts">
import { ref, watch, computed } from "vue";
import { Bar, Line, Pie, Doughnut } from "vue-chartjs";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
);

const props = defineProps<{
  type: "line" | "bar" | "pie" | "doughnut";
  data: Record<string, unknown>;
  options?: Record<string, unknown>;
}>();

const chartRef = ref<InstanceType<
  typeof Line | typeof Bar | typeof Pie | typeof Doughnut
> | null>(null);
const chartData = ref(props.data);
const chartOptions = ref(props.options || {});

const isDark = computed(
  () => document.documentElement.getAttribute("data-theme") === "dark",
);

const themeAwareOptions = computed(() => {
  const base = { ...chartOptions.value };
  if (!base.scales) base.scales = {};
  if (props.type === "line" || props.type === "bar") {
    if (!base.scales.x) base.scales.x = {};
    if (!base.scales.y) base.scales.y = {};
    base.scales.x.ticks = {
      ...base.scales.x.ticks,
      color: isDark.value ? "#94A3B8" : "#6B7280",
    };
    base.scales.y.ticks = {
      ...base.scales.y.ticks,
      color: isDark.value ? "#94A3B8" : "#6B7280",
    };
    base.scales.x.grid = {
      ...base.scales.x.grid,
      color: isDark.value ? "#334155" : "#E5E7EB",
    };
    base.scales.y.grid = {
      ...base.scales.y.grid,
      color: isDark.value ? "#334155" : "#E5E7EB",
    };
  }
  if (!base.plugins) base.plugins = {};
  base.plugins.legend = {
    ...base.plugins.legend,
    labels: { color: isDark.value ? "#E2E8F0" : "#1A1A2E" },
  };
  return base;
});

watch(
  () => props.data,
  (newData) => {
    chartData.value = newData;
  },
  { deep: true },
);

watch(
  () => props.options,
  (newOptions) => {
    chartOptions.value = newOptions || {};
  },
  { deep: true },
);

function pushDataPoint(point: Record<string, unknown>) {
  if (!chartData.value || !chartRef.value) return;
  const datasets = chartData.value.datasets as Array<Record<string, unknown>>;
  if (datasets && datasets[0]) {
    datasets[0].data.push(point);
    if (datasets[0].data.length > 60) {
      datasets[0].data.shift();
    }
  }
  if (chartData.value.labels) {
    (chartData.value.labels as string[]).push((point.label as string) || "");
    if ((chartData.value.labels as string[]).length > 60) {
      (chartData.value.labels as string[]).shift();
    }
  }
}

defineExpose({ pushDataPoint });
</script>

<template>
  <div class="chart-wrapper">
    <Bar
      v-if="type === 'bar'"
      ref="chartRef"
      :data="chartData"
      :options="themeAwareOptions"
    />
    <Line
      v-if="type === 'line'"
      ref="chartRef"
      :data="chartData"
      :options="themeAwareOptions"
    />
    <Pie
      v-if="type === 'pie'"
      ref="chartRef"
      :data="chartData"
      :options="themeAwareOptions"
    />
    <Doughnut
      v-if="type === 'doughnut'"
      ref="chartRef"
      :data="chartData"
      :options="themeAwareOptions"
    />
  </div>
</template>

<style scoped>
.chart-wrapper {
  width: 100%;
  min-height: 200px;
  position: relative;
}
</style>
