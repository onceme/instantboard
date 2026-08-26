/**
 * Finance store price alerts (finance-tab.md §3.2): the alert_update SSE
 * handler pushes alerts newest-first capped at 5, and mirrors them to a
 * browser Notification only when the tab is hidden and the permission was
 * already granted (the store must never request permission itself). Also
 * covers updateWatchlistAlert → PATCH payload + local state merge.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api/finance", () => ({
  financeApi: {
    updateWatchlistItem: vi.fn(),
  },
}));

import { financeApi } from "@/api/finance";
import { useFinanceStore } from "@/stores/finance";
import type { FinanceAlert, WatchlistItem } from "@/types";

const mockUpdateWatchlistItem = vi.mocked(financeApi.updateWatchlistItem);

function makeAlert(overrides: Partial<FinanceAlert> = {}): FinanceAlert {
  return {
    symbol: "AAPL",
    name: "Apple Inc",
    price: 231.5,
    change_percent: 2.4,
    threshold_percent: 2,
    direction: "up",
    triggered_at: "2026-08-26T10:00:00Z",
    ...overrides,
  };
}

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    value: state,
    configurable: true,
  });
}

class MockNotification {
  static permission: NotificationPermission = "default";
  constructor(
    public title: string,
    public options?: NotificationOptions,
  ) {
    MockNotification.instances.push(this);
  }
  static instances: MockNotification[] = [];
}

describe("updateAlertFromSSE", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    MockNotification.instances = [];
    MockNotification.permission = "default";
    vi.stubGlobal("Notification", MockNotification);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setVisibility("visible");
  });

  it("pushes the alert to the front and caps history at 5 entries", () => {
    const store = useFinanceStore();

    for (let i = 1; i <= 7; i++) {
      store.updateAlertFromSSE(
        makeAlert({ symbol: `S${i}`, triggered_at: `t${i}` }),
      );
    }

    expect(store.alerts).toHaveLength(5);
    // Newest first: S7 pushed last sits at index 0
    expect(store.alerts.map((a) => a.symbol)).toEqual([
      "S7",
      "S6",
      "S5",
      "S4",
      "S3",
    ]);
  });

  it("keeps the full payload structure of the pushed alert", () => {
    const store = useFinanceStore();
    const alert = makeAlert();

    store.updateAlertFromSSE(alert);

    expect(store.alerts[0]).toEqual(alert);
  });

  it("sends a browser notification when the tab is hidden and permission granted", () => {
    MockNotification.permission = "granted";
    setVisibility("hidden");
    const store = useFinanceStore();

    store.updateAlertFromSSE(makeAlert());

    expect(MockNotification.instances).toHaveLength(1);
    const note = MockNotification.instances[0];
    expect(note.title).toContain("AAPL");
    expect(note.options?.body).toContain("+2.40%");
  });

  it("does not notify while the tab is visible even with permission granted", () => {
    MockNotification.permission = "granted";
    setVisibility("visible");
    const store = useFinanceStore();

    store.updateAlertFromSSE(makeAlert());

    expect(MockNotification.instances).toHaveLength(0);
  });

  it("does not notify when permission is not granted", () => {
    MockNotification.permission = "denied";
    setVisibility("hidden");
    const store = useFinanceStore();

    store.updateAlertFromSSE(makeAlert());

    expect(MockNotification.instances).toHaveLength(0);
  });

  it("does not notify when Notification API is unavailable", () => {
    vi.stubGlobal("Notification", undefined);
    setVisibility("hidden");
    const store = useFinanceStore();

    expect(() => store.updateAlertFromSSE(makeAlert())).not.toThrow();
    expect(store.alerts).toHaveLength(1);
    expect(MockNotification.instances).toHaveLength(0);
  });
});

describe("updateWatchlistAlert", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("PATCHes the threshold and merges the result into the local watchlist", async () => {
    const store = useFinanceStore();
    const item: WatchlistItem = {
      id: "w1",
      symbol: "AAPL",
      display_order: 0,
      alert_threshold_percent: undefined,
      created_at: "2026-08-26T00:00:00Z",
    };
    store.watchlist = [item];
    mockUpdateWatchlistItem.mockResolvedValueOnce({
      success: true,
      data: { ...item, alert_threshold_percent: 3 },
    });

    await store.updateWatchlistAlert("w1", 3);

    expect(mockUpdateWatchlistItem).toHaveBeenCalledWith("w1", {
      alert_threshold_percent: 3,
    });
    expect(store.watchlist[0].alert_threshold_percent).toBe(3);
  });

  it("sends null to disable the alert and clears the local value", async () => {
    const store = useFinanceStore();
    const item: WatchlistItem = {
      id: "w1",
      symbol: "AAPL",
      display_order: 0,
      alert_threshold_percent: 3,
      created_at: "2026-08-26T00:00:00Z",
    };
    store.watchlist = [item];
    mockUpdateWatchlistItem.mockResolvedValueOnce({
      success: true,
      data: { ...item, alert_threshold_percent: undefined },
    });

    await store.updateWatchlistAlert("w1", null);

    expect(mockUpdateWatchlistItem).toHaveBeenCalledWith("w1", {
      alert_threshold_percent: null,
    });
    expect(store.watchlist[0].alert_threshold_percent).toBeUndefined();
  });

  it("rethrows backend errors for the caller to display", async () => {
    const store = useFinanceStore();
    store.watchlist = [
      {
        id: "w1",
        symbol: "AAPL",
        display_order: 0,
        created_at: "2026-08-26T00:00:00Z",
      },
    ];
    mockUpdateWatchlistItem.mockRejectedValueOnce(new Error("400"));

    await expect(store.updateWatchlistAlert("w1", 0.1)).rejects.toThrow("400");
  });
});
