/**
 * SystemStatus disk I/O regression (P2-20): the backend now reports disk I/O
 * rates (disk_read_mbps / disk_write_mbps, MB/s — app/services/dashboard.py
 * sample_disk_rates()) and the disk area must render them like the network
 * row: value + unit when present, "--" when missing/zero.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import type { Pinia } from "pinia";
import { createPinia, setActivePinia } from "pinia";

import SystemStatus from "@/components/dashboard/SystemStatus.vue";
import { useDashboardStore } from "@/stores/dashboard";
import type { DashboardSystemInfo } from "@/types";

function makeSystemInfo(
  overrides: Partial<DashboardSystemInfo> = {},
): DashboardSystemInfo {
  return {
    version: "1.0.0",
    uptime_seconds: 3600,
    environment: "development",
    python_version: "3.12.3",
    cpu_count: 4,
    cpu_usage_percent: 25,
    memory_total_mb: 8192,
    memory_used_mb: 4096,
    disk_total_gb: 100,
    disk_used_gb: 40,
    ...overrides,
  };
}

let pinia: Pinia;

function mountWithSystemInfo(info: DashboardSystemInfo | null) {
  const store = useDashboardStore();
  store.systemInfo = info;
  return mount(SystemStatus, { global: { plugins: [pinia] } });
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

describe("disk I/O rates", () => {
  it("renders read/write rates with MB/s unit", () => {
    const wrapper = mountWithSystemInfo(
      makeSystemInfo({ disk_read_mbps: 12.34, disk_write_mbps: 5.6 }),
    );

    const row = wrapper.find(".disk-io-row");
    expect(row.exists()).toBe(true);

    const items = row.findAll(".network-item");
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("磁盘读");
    expect(items[0].text()).toContain("12.34 MB/s");
    expect(items[1].text()).toContain("磁盘写");
    expect(items[1].text()).toContain("5.6 MB/s");
  });

  it("renders -- when the rates are missing", () => {
    const wrapper = mountWithSystemInfo(makeSystemInfo());

    const items = wrapper.findAll(".disk-io-row .network-item");
    expect(items).toHaveLength(2);
    expect(items[0].find(".network-value").text()).toBe("--");
    expect(items[1].find(".network-value").text()).toBe("--");
  });

  it("renders -- when the rates are zero (idle disk)", () => {
    const wrapper = mountWithSystemInfo(
      makeSystemInfo({ disk_read_mbps: 0, disk_write_mbps: 0 }),
    );

    const values = wrapper.findAll(".disk-io-row .network-value");
    expect(values.map((v) => v.text())).toEqual(["--", "--"]);
  });

  it("omits the disk row entirely without system info", () => {
    const wrapper = mountWithSystemInfo(null);
    expect(wrapper.find(".disk-io-row").exists()).toBe(false);
  });
});
