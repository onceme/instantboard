/**
 * Dashboard store source-health SSE regression (docs/design/data-flow.md
 * §3.5.4 source_health_update contract). The backend publishes the full
 * source_health row keyed by source_id; updateSourceHealthFromSSE must merge
 * the mutable health fields into the matching row (never overwriting
 * id/name/source_type), recompute the summary counters, and ignore events for
 * unknown sources. The old implementation matched on data.id — a field absent
 * from the payload — and replaced the whole row, so the DataSourcesHealth
 * table never refreshed from SSE.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useDashboardStore } from "@/stores/dashboard";
import type { DataSourceHealthDetail, SourceHealthUpdateEvent } from "@/types";

const SOURCE_A_ID = "3f2c9a1e-0b6d-4e8f-9a12-b7c5d4e3f2a1";
const SOURCE_B_ID = "8d0a1b2c-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

function makeRow(
  overrides: Partial<DataSourceHealthDetail> = {},
): DataSourceHealthDetail {
  return {
    id: SOURCE_A_ID,
    name: "Hacker News",
    source_type: "rss",
    status: "healthy",
    success_rate_24h: 0.99,
    avg_response_time_ms: 250,
    last_success_at: "2026-08-11T07:00:00+00:00",
    last_failure_at: "2026-08-10T07:00:00+00:00",
    consecutive_failures: 0,
    total_fetches_24h: 288,
    ...overrides,
  };
}

// Real payload shape published by backend
// app/services/sse.py build_source_health_update_payload().
function makePayload(
  overrides: Partial<SourceHealthUpdateEvent> = {},
): SourceHealthUpdateEvent {
  return {
    source_id: SOURCE_A_ID,
    name: "Hacker News",
    source_type: "rss",
    status: "degraded",
    previous_status: "healthy",
    last_error: "connection timeout",
    last_success_at: "2026-08-11T08:00:00+00:00",
    last_failure_at: "2026-08-11T08:05:00+00:00",
    avg_response_time_ms: 432,
    consecutive_failures: 3,
    success_count_24h: 20,
    total_fetches_24h: 23,
    success_rate_24h: 20 / 23,
    timestamp: "2026-08-11T08:05:01+00:00",
    ...overrides,
  };
}

function seedStore(rows: DataSourceHealthDetail[]) {
  const store = useDashboardStore();
  store.dataSources = {
    total_sources: rows.length,
    healthy: rows.filter((r) => r.status === "healthy").length,
    degraded: rows.filter((r) => r.status === "degraded").length,
    down: rows.filter((r) => r.status === "down").length,
    sources: rows,
  };
  return store;
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("updateSourceHealthFromSSE", () => {
  it("updates the matching row's status/times/latency by source_id", () => {
    const store = seedStore([makeRow()]);

    store.updateSourceHealthFromSSE(makePayload());

    expect(store.dataSources).not.toBeNull();
    const row = store.dataSources!.sources[0];
    expect(row.status).toBe("degraded");
    expect(row.last_success_at).toBe("2026-08-11T08:00:00+00:00");
    expect(row.last_failure_at).toBe("2026-08-11T08:05:00+00:00");
    expect(row.avg_response_time_ms).toBe(432);
    expect(row.consecutive_failures).toBe(3);
    expect(row.total_fetches_24h).toBe(23);
    expect(row.success_rate_24h).toBeCloseTo(20 / 23);
    expect(row.last_error).toBe("connection timeout");
  });

  it("preserves identity columns id/name/source_type", () => {
    const store = seedStore([makeRow()]);

    // Even a payload carrying different name/source_type must not overwrite
    // the row's identity columns — they are not mutable via health events.
    store.updateSourceHealthFromSSE(
      makePayload({ name: "RENAMED", source_type: "api" }),
    );

    const row = store.dataSources!.sources[0];
    expect(row.id).toBe(SOURCE_A_ID);
    expect(row.name).toBe("Hacker News");
    expect(row.source_type).toBe("rss");
    // ...while mutable fields are still applied
    expect(row.status).toBe("degraded");
  });

  it("recomputes healthy/degraded/down counters after the update", () => {
    const store = seedStore([
      makeRow(),
      makeRow({ id: SOURCE_B_ID, name: "Eastmoney", status: "healthy" }),
    ]);
    expect(store.dataSources!.healthy).toBe(2);
    expect(store.dataSources!.degraded).toBe(0);
    expect(store.dataSources!.down).toBe(0);

    store.updateSourceHealthFromSSE(makePayload({ status: "down" }));

    expect(store.dataSources!.healthy).toBe(1);
    expect(store.dataSources!.degraded).toBe(0);
    expect(store.dataSources!.down).toBe(1);
    expect(store.dataSources!.total_sources).toBe(2);
  });

  it("does not touch other rows", () => {
    const store = seedStore([
      makeRow(),
      makeRow({ id: SOURCE_B_ID, name: "Eastmoney", status: "healthy" }),
    ]);

    store.updateSourceHealthFromSSE(makePayload({ status: "down" }));

    const other = store.dataSources!.sources[1];
    expect(other.status).toBe("healthy");
    expect(other.avg_response_time_ms).toBe(250);
    expect(other.last_success_at).toBe("2026-08-11T07:00:00+00:00");
  });

  it("ignores events for unknown source_id", () => {
    const store = seedStore([
      makeRow(),
      makeRow({ id: SOURCE_B_ID, name: "Eastmoney" }),
    ]);
    const before = JSON.parse(JSON.stringify(store.dataSources));

    store.updateSourceHealthFromSSE(
      makePayload({ source_id: "unknown-source-id", status: "down" }),
    );

    expect(JSON.parse(JSON.stringify(store.dataSources))).toEqual(before);
  });

  it("keeps last_success_at when the event carries null (first failure)", () => {
    const store = seedStore([makeRow()]);

    store.updateSourceHealthFromSSE(
      makePayload({ status: "degraded", last_success_at: null }),
    );

    const row = store.dataSources!.sources[0];
    // null in the event means "no change" for timestamps, so the previously
    // known value survives instead of being wiped.
    expect(row.last_success_at).toBe("2026-08-11T07:00:00+00:00");
    expect(row.last_failure_at).toBe("2026-08-11T08:05:00+00:00");
  });

  it("does not crash when dataSources has not loaded yet", () => {
    const store = useDashboardStore();
    expect(store.dataSources).toBeNull();

    expect(() => store.updateSourceHealthFromSSE(makePayload())).not.toThrow();
    expect(store.dataSources).toBeNull();
  });
});
