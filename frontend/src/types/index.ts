export interface ApiResponse<T> {
  success: boolean;
  data: T;
  meta?: {
    total: number;
    page: number;
    page_size: number;
  };
}

export interface ApiError {
  success: false;
  error: {
    code: string;
    message: string;
    details?: Array<{ field: string; message: string }>;
  };
}

export interface PaginatedResponse<T> extends ApiResponse<T[]> {
  meta: {
    total: number;
    page: number;
    page_size: number;
  };
}

export enum SSEEventType {
  QUOTE_UPDATE = "quote_update",
  MARKET_INDEX_UPDATE = "market_index_update",
  COMMODITY_UPDATE = "commodity_update",
  NAV_ESTIMATE_UPDATE = "nav_estimate_update",
  ALERT_UPDATE = "alert_update",
  ITEM_UPDATE = "item_update",
  TOPIC_STATS_UPDATE = "topic_stats_update",
  SOURCE_HEALTH_UPDATE = "source_health_update",
  SYSTEM_METRIC_UPDATE = "system_metric_update",
  DB_METRIC_UPDATE = "db_metric_update",
  BUSINESS_METRIC_UPDATE = "business_metric_update",
  HEARTBEAT = "heartbeat",
}

export enum SSEConnectionState {
  CONNECTING = "connecting",
  CONNECTED = "connected",
  DISCONNECTED = "disconnected",
  RECONNECTING = "reconnecting",
}

// Auth types
export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
  tenant_id: string;
  role: "admin" | "member" | "viewer";
  sso_provider?: string;
  preferences?: UserPreferences;
}

// All fields optional: the login response may embed a full blob, while
// GET/PUT /users/me/preferences only manage (and return) favorite_tags.
// Consumers must truthy-guard each key (see useAuthStore.applyUserPreferences).
export interface UserPreferences {
  color_scheme?: "chinese" | "international";
  theme?: "light" | "dark";
  favorite_tags?: string[];
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface SSOProvider {
  name: string;
  slug: "google" | "azure_ad" | "github" | "apple" | "facebook";
  authorize_url: string;
  brand_color: string;
  icon: string;
}

// Finance types
export interface FinanceQuote {
  symbol: string;
  name: string;
  current_price: number;
  open?: number;
  high?: number;
  low?: number;
  close_previous?: number;
  volume?: number;
  change: number;
  change_percent: number;
  market_cap?: number;
  pe_ratio?: number;
  "52_week_high"?: number;
  "52_week_low"?: number;
  timestamp: string;
  source?: string;
  currency?: string;
  type?: "stock" | "fund" | "index" | "commodity";
  market?: string;
  exchange?: string;
}

export interface MarketIndex {
  symbol: string;
  name: string;
  value: number;
  change: number;
  change_percent: number;
  market_status: "open" | "closed" | "pre_market" | "post_market";
  // Must match the region values in the backend MARKET_INDICES_CONFIG (GB=FTSE 100, DE=DAX, FR=CAC 40)
  region: "US" | "CN" | "HK" | "JP" | "GB" | "DE" | "FR" | "KR" | "IN";
  timestamp: string;
}

export interface Commodity {
  symbol: string;
  name: string;
  value: number;
  change: number;
  change_percent: number;
  unit: string;
  category: "precious_metal" | "energy" | "industrial_metal" | "agriculture";
  timestamp: string;
}

export interface FundNAV {
  symbol: string;
  name: string;
  nav_official: number;
  nav_official_date: string;
  nav_estimate?: number;
  nav_estimate_deviation_percent?: number;
  estimate_method?: "index_tracking" | "latest_official" | "none";
  estimate_timestamp?: string;
  underlying_index?: {
    symbol: string;
    name: string;
    current_value: number;
    change_percent: number;
  };
}

export interface WatchlistItem {
  id: string;
  symbol: string;
  name?: string;
  display_order: number;
  alert_threshold_percent?: number;
  created_at: string;
}

export interface WatchlistQuote {
  symbol: string;
  name: string;
  current_price: number;
  change: number;
  change_percent: number;
  volume?: number;
  timestamp: string;
}

export interface SearchResult {
  symbol: string;
  name: string;
  type: "stock" | "fund" | "index" | "commodity";
  market: string;
  exchange: string;
  current_price?: number;
  change_percent?: number;
  currency?: string;
}

// Tech types
export interface TechNewsItem {
  id: string;
  title: string;
  summary?: string;
  url: string;
  // Backend TechNewsResponse.source_name is str | None (the generic
  // /categories/{id}/items feed also uses this shape)
  source_name: string | null;
  source_id: string;
  category_id: string;
  topic_tags: string[];
  published_at: string;
  fetched_at: string;
  image_url?: string;
  priority: number;
  extra_data?: Record<string, unknown>;
}

export interface TechTopic {
  tag: string;
  label: string;
  level: 1 | 2 | 3;
  count: number;
  trending_change?: number;
  domain?: string;
}

export type TechDomain = "robotics" | "ai" | "embedded" | "space" | "all";
export type TechSort = "hot" | "time" | "relevance";

export const DOMAIN_CONFIG: Record<
  string,
  { slug: string; label: string; color: string; icon: string }
> = {
  robotics: { slug: "robotics", label: "机器人", color: "#8B5CF6", icon: "🤖" },
  ai: { slug: "ai", label: "人工智能", color: "#3B82F6", icon: "🧠" },
  embedded: {
    slug: "embedded",
    label: "大规模嵌入式",
    color: "#F59E0B",
    icon: "⚡",
  },
  space: { slug: "space", label: "太空科技", color: "#10B981", icon: "🚀" },
};

export const SUBCATEGORY_MAP: Record<
  string,
  Array<{ slug: string; label: string }>
> = {
  robotics: [
    { slug: "humanoid", label: "人形机器人" },
    { slug: "industrial", label: "工业机器人" },
    { slug: "cobot", label: "协作机器人" },
    { slug: "autonomous-driving", label: "自动驾驶" },
    { slug: "drone", label: "无人机" },
    { slug: "robot-software", label: "机器人OS/软件" },
  ],
  ai: [
    { slug: "llm", label: "大语言模型" },
    { slug: "generative-ai", label: "生成式AI" },
    { slug: "ai-hardware", label: "AI芯片/硬件" },
    { slug: "ai-ethics", label: "AI伦理与治理" },
    { slug: "multimodal", label: "多模态AI" },
    { slug: "ai-agent", label: "AI Agent/应用" },
  ],
  embedded: [
    { slug: "iot-edge", label: "IoT与边缘计算" },
    { slug: "risc-v", label: "RISC-V与处理器" },
    { slug: "rtos", label: "实时操作系统" },
    { slug: "fpga", label: "FPGA与硬件加速" },
    { slug: "chip-design", label: "芯片设计" },
    { slug: "embedded-ai", label: "嵌入式AI" },
  ],
  space: [
    { slug: "commercial-space", label: "商业航天" },
    { slug: "satellite-internet", label: "卫星互联网" },
    { slug: "deep-space", label: "深空探测" },
    { slug: "orbital", label: "空间站与在轨服务" },
    { slug: "rocket-tech", label: "火箭技术" },
    { slug: "space-manufacturing", label: "太空制造与资源" },
  ],
};

// Dashboard types
// API request stats group of GET /dashboard/system — backend
// app/services/dashboard.py get_api_request_stats() fed by
// RequestLoggingMiddleware. All zeros on fresh start / Redis downgrade.
export interface DashboardApiStats {
  // Requests/s over a sliding 60s window.
  qps: number;
  // Average response time (ms) over the same 60s window.
  avg_response_ms: number;
  // 4xx / 5xx share of windowed requests, 0..1 ratios (frontend shows %).
  error_rate_4xx: number;
  error_rate_5xx: number;
  // Cumulative requests since the Redis totals key was created.
  requests_total: number;
}

export interface DashboardSystemInfo {
  version: string;
  uptime_seconds: number;
  environment: string;
  python_version: string;
  cpu_count: number;
  cpu_usage_percent: number;
  memory_total_mb: number;
  memory_used_mb: number;
  disk_total_gb: number;
  disk_used_gb: number;
  // Disk I/O rates (MB/s) — backend app/services/dashboard.py
  // sample_disk_rates(); 0 on the first sample or when psutil is unavailable.
  disk_read_mbps?: number;
  disk_write_mbps?: number;
  network_in_kbps?: number;
  network_out_kbps?: number;
  // Optional for backward compatibility with older backends that do not
  // report the api group yet.
  api?: DashboardApiStats;
  api_version?: string;
}

export interface ServiceHealth {
  service: string;
  status: "healthy" | "degraded" | "down";
  response_time_ms: number;
  connection_count?: number;
  details?: Record<string, unknown>;
}

export interface DataSourceHealthSummary {
  total_sources: number;
  healthy: number;
  degraded: number;
  down: number;
  sources: DataSourceHealthDetail[];
}

// SSE payload of "source_health_update" (dashboard channel).
// Contract: docs/dev-guide/design/data-flow.md §3.5.4; built by backend
// app/services/sse.py build_source_health_update_payload(). The health table
// row is matched by source_id === DataSourceHealthDetail.id.
export interface SourceHealthUpdateEvent {
  source_id: string;
  name: string;
  source_type: string | null;
  status: "healthy" | "degraded" | "down";
  previous_status: string;
  last_error: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  avg_response_time_ms: number;
  consecutive_failures: number;
  success_count_24h: number;
  total_fetches_24h: number;
  success_rate_24h: number | null;
  timestamp: string;
}

export interface DataSourceHealthDetail {
  id: string;
  name: string;
  // Backend schemas/dashboard.py DataSourceHealthDetail: str | None
  source_type?: string | null;
  status: "healthy" | "degraded" | "down";
  success_rate_24h: number;
  avg_response_time_ms: number;
  last_success_at?: string;
  last_failure_at?: string;
  consecutive_failures: number;
  total_fetches_24h: number;
  last_error?: string;
}

// GET /api/v1/dashboard/data-sources/{source_id}
// (backend schemas/dashboard.py DataSourceHealthDetailResponse).
// success_rate_24h is a 0..1 ratio (success_count_24h / total_fetches_24h),
// null when there were no fetches in the window. health_history entries are
// cached source_health snapshots; response_time_trend entries carry a
// timestamp (ts/time/timestamp) and a millisecond value (ms).
export interface DataSourceHealthDetailResponse {
  source_id: string;
  name: string;
  source_type?: string | null;
  status: "healthy" | "degraded" | "down";
  success_rate_24h: number | null;
  avg_response_time_ms: number | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  consecutive_failures: number | null;
  total_fetches_24h: number | null;
  last_error?: string | null;
  health_history?: Array<Record<string, unknown>> | null;
  response_time_trend?: Array<Record<string, unknown>> | null;
}

// GET /api/v1/dashboard/scheduler (admin only) — backend
// schemas/dashboard.py SchedulerJobInfo. success/failure counters and
// last_run may be null when the scheduler has no stats for the window.
export interface SchedulerJobInfo {
  job_id: string;
  source_id?: string | null;
  name: string;
  schedule?: string | null;
  original_interval?: number | null;
  current_interval?: number | null;
  adaptive_multiplier?: number | null;
  last_run?: string | null;
  next_run?: string | null;
  status: "active" | "paused" | "error";
  success_count_24h?: number | null;
  failure_count_24h?: number | null;
}

// GET /api/v1/dashboard/scheduler — backend SchedulerStatusResponse. In dev
// (embedded scheduler) the per-job lists are populated and the *_count fields
// mirror their lengths; in prod the lists are empty (jobs live in the worker
// container) and counts + last_heartbeat come from the worker heartbeat,
// written every 15s with a 45s Redis TTL.
export interface SchedulerStatusResponse {
  total_jobs: number;
  running_jobs: SchedulerJobInfo[];
  paused_jobs: SchedulerJobInfo[];
  all_jobs: SchedulerJobInfo[];
  running_jobs_count: number;
  paused_jobs_count: number;
  last_heartbeat?: string | null;
}

export interface SSEStats {
  total_connections: number;
  connections_by_channel: Record<string, number>;
  peak_connections_24h: number;
  events_pushed_24h: number;
}

// GET /api/v1/dashboard/business-metrics (admin only) — backend
// schemas/dashboard.py BusinessMetricsResponse. One entry of the
// category distribution list.
export interface BusinessCategoryCount {
  category_name: string;
  count: number;
}

// All counters are system-wide (all tenants). Any unavailable sub-metric
// degrades to 0 / empty list on the backend instead of erroring.
export interface BusinessMetrics {
  // Distinct users with an SSE connection within the last 24 hours.
  active_users_24h: number;
  // Items created since today 00:00 UTC.
  items_today: number;
  // Items grouped by category, sorted descending by count.
  category_distribution: BusinessCategoryCount[];
  // Total watchlist entries.
  watchlist_total: number;
  // SSE push events counted over the sliding 60-minute window.
  events_pushed_1h: number;
}

// Category & Source types
export interface Category {
  id: string;
  name: string;
  slug: string;
  description?: string;
  icon?: string;
  color?: string;
  type: "finance" | "tech" | "news" | "custom";
  refresh_interval_seconds: number;
  is_active: boolean;
  source_count?: number;
  keywords_filter?: string[];
  created_at: string;
  updated_at: string;
}

// Bulk re-tagging result for POST /categories/{id}/reclassify
export interface ReclassifyResult {
  scanned: number;
  updated: number;
}

// Tenant-level category overrides stored in tenants.settings. refresh_overrides
// maps category slugs to effective refresh intervals (seconds, 10-86400);
// color_overrides maps slugs to display colors (#RRGGBB). Both maps are
// wholesale-replaced by PUT /api/v1/tenant/settings (a missing slug = cleared).
export interface TenantSettings {
  refresh_overrides: Record<string, number>;
  color_overrides: Record<string, string>;
}

// PUT body: both maps optional; the backend replaces each provided map
// wholesale (type alias, not interface, so it stays assignable to the api
// client's Record<string, unknown> payload param)
export type TenantSettingsUpdate = {
  refresh_overrides?: Record<string, number>;
  color_overrides?: Record<string, string>;
};

export interface Source {
  id: string;
  name: string;
  category_id: string;
  source_type: "rss" | "api" | "web_scrape" | "social";
  url: string;
  config?: Record<string, unknown>;
  refresh_interval_seconds: number;
  is_active: boolean;
  health_status: "healthy" | "degraded" | "down";
  // Backend schemas/source.py: false = no collector registered for this source
  // type (cannot be enabled); undefined = legacy payloads, treat as unknown
  collector_available?: boolean;
  last_fetch_at?: string;
  last_error?: string;
  created_at: string;
  updated_at: string;
}

// Finance sub-nav panel type
export type FinancePanel =
  "overview" | "watchlist" | "search" | "indices" | "commodities";

// Market region groups
// Region values align with the backend MARKET_INDICES_CONFIG: GB=FTSE 100/London, DE=DAX, FR=CAC 40, grouped under Europe
export const MARKET_REGION_GROUPS: Record<
  string,
  { label: string; regions: string[] }
> = {
  US: { label: "美国", regions: ["US"] },
  CN: { label: "中国", regions: ["CN", "HK"] },
  APAC: { label: "亚太", regions: ["JP", "KR", "IN"] },
  EU: { label: "欧洲", regions: ["GB", "DE", "FR"] },
};

export const COMMODITY_GROUPS: Record<
  string,
  { label: string; category: string }
> = {
  precious_metal: { label: "贵金属", category: "precious_metal" },
  energy: { label: "能源", category: "energy" },
  industrial_metal: { label: "工业金属", category: "industrial_metal" },
  agriculture: { label: "农产品", category: "agriculture" },
};
