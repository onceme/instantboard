/**
 * Fund estimate status-note mapping (fund-intraday-nav.md §9.4): the four
 * states a fund row can be in when no live intraday estimate is available,
 * derived purely from the batch payload fields.
 */
import { describe, expect, it } from "vitest";
import { getFundStatusNote } from "@/utils/fundStatus";
import type { FundNAVIntraday } from "@/types";

function nav(overrides: Partial<FundNAVIntraday> = {}): FundNAVIntraday {
  return {
    symbol: "017811",
    name: "东方人工智能主题混合C",
    nav_official: null,
    nav_official_date: null,
    nav_estimate: null,
    estimate_change_percent: null,
    estimate_method: "latest_official",
    coverage_percent: null,
    holdings_report_date: null,
    quote_status: "frozen",
    delayed_markets: [],
    holdings_stale: false,
    estimate_timestamp: "2026-09-01T06:47:13+00:00",
    ...overrides,
  };
}

describe("getFundStatusNote", () => {
  it("returns null for absent or error-marked snapshots", () => {
    expect(getFundStatusNote(null)).toBeNull();
    expect(getFundStatusNote(undefined)).toBeNull();
    expect(getFundStatusNote(nav({ error: "unknown code" }))).toBeNull();
  });

  it("frozen without an official anchor → 官方净值待更新 (freshly followed fund)", () => {
    // The real 017811 shape observed in staging: anchor and holdings both
    // missing before the nightly 20:00 backfill.
    const note = getFundStatusNote(nav());
    expect(note?.label).toBe("官方净值待更新");
    expect(note?.tooltip).toContain("20:00");
  });

  it("stale/anomalous disclosure without a live estimate → 盘中估值不可用·持仓披露异常", () => {
    const note = getFundStatusNote(
      nav({
        holdings_stale: true,
        holdings_report_date: "2022-06-30",
        nav_official: 1.234,
        nav_estimate: 1.234,
      }),
    );
    expect(note?.label).toBe("盘中估值不可用·持仓披露异常");
    expect(note?.tooltip).toContain("120 天");
  });

  it("stale disclosure with a live index-tracking estimate keeps no note (⚠ badge covers it)", () => {
    const note = getFundStatusNote(
      nav({
        holdings_stale: true,
        holdings_report_date: "2022-06-30",
        estimate_method: "index_tracking",
        nav_official: 1.234,
        nav_estimate: 1.25,
        estimate_change_percent: 1.3,
        quote_status: "realtime",
      }),
    );
    expect(note).toBeNull();
  });

  it("holdings ingesting without a live estimate → 持仓数据摄取中… (just searched/followed)", () => {
    const note = getFundStatusNote(nav({ holdings_ingesting: true }));
    expect(note?.label).toBe("持仓数据摄取中…");
    expect(note?.tooltip).toContain("官方净值每晚 20:00 左右更新");
  });

  it("holdings ingesting with a live estimate → no note (estimate is real)", () => {
    const note = getFundStatusNote(
      nav({
        holdings_ingesting: true,
        estimate_method: "index_tracking",
        nav_official: 1.0,
        nav_estimate: 1.02,
        estimate_change_percent: 2.0,
        quote_status: "realtime",
      }),
    );
    expect(note).toBeNull();
  });

  it("holdings ingesting outranks 官方净值待更新", () => {
    // Freshly followed fund: anchor missing AND ingestion in flight — the
    // specific in-flight explanation wins over the generic anchor note.
    const note = getFundStatusNote(nav({ holdings_ingesting: true }));
    expect(note?.label).toBe("持仓数据摄取中…");
  });

  it("disclosure anomaly outranks 持仓数据摄取中…", () => {
    const note = getFundStatusNote(
      nav({ holdings_ingesting: true, holdings_stale: true }),
    );
    expect(note?.label).toBe("盘中估值不可用·持仓披露异常");
  });

  it("held anchor with latest_official method → 净值停更 (user-chosen wording)", () => {
    const note = getFundStatusNote(
      nav({
        nav_official: 2.05,
        nav_official_date: "2026-08-29",
        nav_estimate: 2.05,
      }),
    );
    expect(note?.label).toBe("净值停更");
  });

  it("disclosure anomaly outranks 净值停更 when both apply", () => {
    const note = getFundStatusNote(
      nav({
        holdings_stale: true,
        nav_official: 2.05,
        nav_official_date: "2026-08-29",
        nav_estimate: 2.05,
      }),
    );
    expect(note?.label).toBe("盘中估值不可用·持仓披露异常");
  });

  it("normal realtime holdings-weighted estimate → no note", () => {
    const note = getFundStatusNote(
      nav({
        estimate_method: "holdings_weighted",
        nav_official: 1.0,
        nav_estimate: 1.01,
        estimate_change_percent: 1.0,
        coverage_percent: 46.2,
        quote_status: "realtime",
        holdings_report_date: "2026-06-30",
      }),
    );
    expect(note).toBeNull();
  });

  it("delayed-quote estimate → no note (delay badge covers it)", () => {
    const note = getFundStatusNote(
      nav({
        estimate_method: "holdings_weighted",
        nav_official: 1.0,
        nav_estimate: 1.0,
        estimate_change_percent: 0.1,
        quote_status: "delayed",
        delayed_markets: ["US"],
      }),
    );
    expect(note).toBeNull();
  });

  it("index-tracking realtime estimate with anchor → no note", () => {
    const note = getFundStatusNote(
      nav({
        estimate_method: "index_tracking",
        nav_official: 1.0,
        nav_estimate: 1.02,
        estimate_change_percent: 2.0,
        quote_status: "realtime",
      }),
    );
    expect(note).toBeNull();
  });
});
