import type { FundNAVIntraday } from "@/types";

// User-facing intraday-estimate status notes (fund-intraday-nav.md §9.4).
// Derived purely from the batch payload fields — no extra backend field:
// an estimate row that cannot show a live number must explain why instead of
// rendering a bare "--".
export interface FundStatusNote {
  label: string;
  tooltip: string;
}

const NOTE_PENDING_OFFICIAL: FundStatusNote = {
  label: "官方净值待更新",
  tooltip:
    "该基金尚无官方净值记录（首次入库或刚加入自选）。官方净值每晚 20:00 左右更新，回灌后即可看到盘中估值。",
};

const NOTE_DISCLOSURE_ANOMALY: FundStatusNote = {
  label: "盘中估值不可用·持仓披露异常",
  tooltip:
    "持仓报告期缺失、超过新鲜度阈值（120 天）或披露异常，无法计算盘中估值；官方净值每晚 20:00 左右更新后仍可参考。",
};

// A fund that was just searched/followed has its holdings fetched lazily
// (§5.1 case 3 / §9 on-demand compute): the estimate appears as soon as the
// snapshot lands (and the nightly 20:00 official NAV anchor is present).
const NOTE_HOLDINGS_INGESTING: FundStatusNote = {
  label: "持仓数据摄取中…",
  tooltip:
    "该基金刚被搜索或加入自选，持仓数据正在抓取（通常数秒到一分钟）。持仓就绪且官方净值在库后，盘中即可看到实时估值；官方净值每晚 20:00 左右更新。",
};

// User-chosen wording for the "only the latest official NAV is available, no
// realtime estimate" situation (e.g. no holdings and no index binding).
const NOTE_NAV_FROZEN: FundStatusNote = {
  label: "净值停更",
  tooltip:
    "暂无实时估值，仅显示最新官方净值（无可用持仓与指数绑定，或估值上游暂停）。官方净值每晚 20:00 左右更新。",
};

/**
 * Status note for a fund estimate snapshot; null means "no extra status"
 * (normal realtime/delayed estimation, or the row already carries the ⚠
 * stale badge alongside a live index-tracking estimate).
 *
 * Priority: disclosure anomaly > holdings ingesting > pending official
 * anchor > NAV frozen:
 * - holdings_stale without a live estimate means the pipeline is blocked by
 *   disclosure, the strongest explanation;
 * - holdings_ingesting without a live estimate is the freshly searched or
 *   just-followed fund: ingestion is in flight and explains the empty rows;
 * - frozen quote plus a missing anchor is the freshly-followed fund state
 *   (nightly 20:00 anchor backfill has not run yet);
 * - a held anchor with the latest_official method is the "净值停更" case.
 */
export function getFundStatusNote(
  nav: FundNAVIntraday | null | undefined,
): FundStatusNote | null {
  if (!nav || nav.error) return null;

  if (nav.holdings_stale) {
    // A stale-disclosure fund that still produces a live index-tracking
    // estimate keeps the existing ⚠ badge; a status note here would wrongly
    // claim the estimate is unavailable.
    if (nav.estimate_method === "index_tracking" && nav.nav_estimate != null) {
      return null;
    }
    return NOTE_DISCLOSURE_ANOMALY;
  }

  if (nav.holdings_ingesting && nav.estimate_change_percent == null) {
    // A fund with an index binding can still show a live estimate while its
    // holdings load — no "ingesting" note then (the estimate is real).
    return NOTE_HOLDINGS_INGESTING;
  }

  if (nav.quote_status === "frozen" && nav.nav_official == null) {
    return NOTE_PENDING_OFFICIAL;
  }

  if (nav.estimate_method === "latest_official" && nav.nav_official != null) {
    return NOTE_NAV_FROZEN;
  }

  return null;
}
