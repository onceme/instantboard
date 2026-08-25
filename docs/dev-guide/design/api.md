---
version: 1.1
author: designer
date: 2026-08-24
status: revised
cross_refs: [architecture.md, database.md, frontend.md, security.md, data-flow.md, admin-login.md]
---

# InstantBoard REST API 与 SSE 设计

## 1. 目标

定义 InstantBoard 所有 REST API 端点和 SSE 推送端点的完整规范，包括路径、方法、参数、响应格式、状态码和错误处理，供 coder 直接实现。

## 2. 方案概述

采用 **RESTful + SSE 双通道** 设计，REST API 用于 CRUD 操作和一次性查询，SSE 用于实时数据推送，统一使用 Pydantic schema 验证请求/响应。

## 3. 详细设计

### 3.1 API 总体规范

**Base URL**: `/api/v1`

**通用响应包装**:
```json
// 成功响应
{
  "success": true,
  "data": { ... },
  "meta": {
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}

// 错误响应
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human readable message",
    "details": [{ "field": "name", "message": "Required" }]
  }
}
```

**通用分页参数** (列表端点):
- `page`: int, default 1, min 1
- `page_size`: int, default 20, min 1, max 100

> ⚠️ **未实现**：通用 `sort_by` / `sort_order` 参数——任何端点都不支持（`schemas/base.py:60-64` 仅存在无人消费的 `PaginationParams` schema）。排序由各端点自行决定（如 tech news 提供 `sort=hot|time|relevance`）。

**认证**: 除下列公开端点与 SSE 外，所有 `/api/v1/*` 端点需要 JWT Token (Bearer Auth)
- Header: `Authorization: Bearer <jwt_token>`
- SSE 端点: 通过 query param `token=<jwt_token>` 认证 (EventSource 不支持自定义 header)
- **公开端点（无需认证）**: `/health`、`/health/detail`、`/auth/sso/providers`、`/auth/sso/{provider}/authorize`、`/auth/sso/{provider}`、`/auth/admin/login`、`/auth/refresh`
- ⚠️ 安全关注: `/health` 与 `/health/detail` 均无认证，后者还会暴露 PostgreSQL/Redis 状态与运行环境名，敏感环境建议由网关限制访问（见 security.md）
- `/dashboard/*` 全部端点需要 **admin 角色**（`require_admin` 依赖），非 admin 返回 403 `FORBIDDEN`
- `/stream/status` 认证**可选**（携带则返回该用户连接状态，否则返回空）

**多租户**: 所有请求自动注入 `tenant_id` (从 JWT claims 中提取)

### 3.2 认证与授权 API

#### GET `/api/v1/auth/sso/providers` — 获取已启用的 SSO 提供商

**描述:** 返回当前配置中启用的 SSO 提供商列表。前端可用此接口动态显示登录按钮。

**认证:** 不需要（公开端点）

```
Response 200:
  {
    "success": true,
    "data": {
      "enabled_providers": ["google", "github"]
    }
  }
```

**配置驱动：**
通过 `ENABLED_SSO_PROVIDERS` 环境变量控制，支持以下值：
- `google` — Google OAuth 2.0
- `github` — GitHub OAuth *(默认启用)*
- `azure_ad` — Azure Active Directory
- `apple` — Apple Sign In
- `facebook` — Facebook Login

**默认值:** `google,github`

**配置示例:**
```bash
# .env
ENABLED_SSO_PROVIDERS=google,github                     # 默认
ENABLED_SSO_PROVIDERS=google,github,azure_ad             # 额外启用 Azure AD
ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook  # 全部启用
```

#### GET `/api/v1/auth/sso/{provider}/authorize` — 获取 OAuth 授权 URL（auth.py:45）

**认证:** 不需要（公开端点）

```
Query Params:
  redirect_uri: str (required) — 授权完成后的回调地址

Response 200:
  {
    "success": true,
    "data": {
      "authorize_url": "https://github.com/login/oauth/authorize?client_id=...",
      "state": "<secrets.token_urlsafe(32)>"
    }
  }

Note: state 生成后直接返回前端，后端**不存储、不校验**（防 CSRF 校验层缺失，见 security.md §3.2）
```

#### POST `/api/v1/auth/sso/{provider}` — SSO 登录

**provider**: `google` | `azure_ad` | `github` | `apple` | `facebook`

> ⚠️ 仅启用列表中的 provider 可被调用；provider 不受支持或未在 `ENABLED_SSO_PROVIDERS` 中时，统一返回 `400 VALIDATION_ERROR`（无独立错误码）。

```
Request:
  POST /api/v1/auth/sso/github
  Body: {
    "code": "oauth_authorization_code",
    "redirect_uri": "https://app.instantboard.dev/auth/callback"
  }

Response 200:
  {
    "success": true,
    "data": {
      "access_token": "eyJ...",
      "refresh_token": "eyJ...",
      "token_type": "Bearer",
      "expires_in": 3600,
      "user": {
        "id": "uuid",
        "email": "user@example.com",
        "name": "John Doe",
        "avatar_url": "https://...",
        "tenant_id": "uuid",
        "role": "member"
      }
    }
  }

Response 400:
  { "success": false, "error": { "code": "INVALID_OAUTH_CODE", ... } }    // 授权码无效
  { "success": false, "error": { "code": "VALIDATION_ERROR", ... } }      // provider 不受支持 / 未启用

Response 502:
  { "success": false, "error": { "code": "SSO_PROVIDER_ERROR", ... } }    // 上游提供商自身故障
  // 注: 状态码为 502 (上游/网关错误) 而非 401——避免前端拦截器误判为会话过期而跳转登录页
```

#### POST `/api/v1/auth/admin/login` — 本地管理员登录（auth.py:84）

**认证:** 不需要（公开端点）。完整设计（身份隔离、防爆破、kill-switch）见 [admin-login.md](admin-login.md)。

```
Request:
  { "email": "admin@example.com", "password": "..." }   // password max_length=72 (bcrypt 上限)

Response 200: 与 SSO 登录相同的 TokenResponse (user.role="admin", user.sso_provider="local", claims.provider="local")
Response 401: { "success": false, "error": { "code": "INVALID_CREDENTIALS" } }   // 密码错误/邮箱未知/锁定中, 统一文案
Response 503: { "success": false, "error": { "code": "ADMIN_LOGIN_DISABLED" } }  // 未配置管理员凭据
```

#### POST `/api/v1/auth/refresh` — 刷新 Token（公开端点）

```
Request:
  { "refresh_token": "eyJ..." }

Response 200:
  { "success": true, "data": { "access_token": "eyJ...", "refresh_token": "eyJ...(新)", "expires_in": 3600 } }
  // 轮换机制: 旧 refresh token 立即进黑名单, 响应返回全新 token 对——包含新的
  // refresh_token (services/auth.py:241-245)。前端必须保存新的 refresh_token。

Response 401:
  { "success": false, "error": { "code": "INVALID_REFRESH_TOKEN" } }
```

#### GET `/api/v1/auth/me` — 当前用户信息

```
Response 200:
  { "success": true, "data": { "id": "uuid", "email": "...", "name": "...", "tenant_id": "...", "role": "...", "sso_provider": "github" } }
```

#### DELETE `/api/v1/auth/logout` — 登出

```
Request (可选 JSON body):
  { "refresh_token": "eyJ..." }
  // 携带时该 refresh token 被加入 Redis 黑名单 (TTL = 配置有效期, 默认 7 天);
  // 当前 access token 无论如何都按剩余有效期拉黑。

Response 200:
  { "success": true, "data": { "message": "Logged out" } }
```

### 3.3 分类管理 CRUD API

#### GET `/api/v1/categories` — 获取分类列表

```
Query Params (categories.py:23-31):
  type: str (optional) — 按分类类型筛选: "finance" | "tech" | "news" | "custom"
  page: int (default 1)
  page_size: int (default 20, max 100)

Note: 列表端点不含数据源明细；读取"分类+数据源"用独立端点
      GET /api/v1/categories/{category_id}/sources（原 include_sources 参数已移除）

Response 200:
  {
    "success": true,
    "data": [
      {
        "id": "uuid",
        "name": "A股行情",
        "slug": "china-stock",
        "description": "中国A股市场行情",
        "icon": "chart-line",
        "color": "#FF6B6B",
        "type": "finance",
        "refresh_interval_seconds": 30,
        "is_active": true,
        "source_count": 5,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z"
      }
    ],
    "meta": { "total": 10, "page": 1, "page_size": 20 }
  }
```

#### POST `/api/v1/categories` — 创建分类

```
Request:
  {
    "name": "自定义分类",        // required, max 50 chars
    "slug": "custom-cat",        // optional, auto-generated from name
    "description": "...",        // optional, max 200 chars
    "icon": "newspaper",         // optional, icon name from icon library
    "color": "#3B82F6",          // optional, hex color
    "type": "custom",            // required: "finance" | "tech" | "news" | "custom"
    "refresh_interval_seconds": 300,  // optional, default 300
    "keywords_filter": ["AI", "机器人"],  // optional,关键词过滤列表
    "is_active": true            // optional, default true
  }

Response 201:
  { "success": true, "data": { ... created category ... } }

Response 400:
  { "success": false, "error": { "code": "VALIDATION_ERROR", ... } }
```

#### GET `/api/v1/categories/predefined` — 预置分类列表（categories.py:55）

返回系统预置的分类（供快速启用/参考），`SuccessResponse[list[CategoryResponse]]`。

#### GET `/api/v1/categories/{category_id}` — 获取单个分类

```
Response 200: { "success": true, "data": { ... CategoryResponse（**不含** sources）... } }
Response 404: { "success": false, "error": { "code": "CATEGORY_NOT_FOUND" } }
Note: 数据源列表拆到独立端点，见下。
```

#### GET `/api/v1/categories/{category_id}/sources` — 分类详情（含数据源）（categories.py:109）

```
Response 200: { "success": true, "data": { ...category 字段..., "sources": [ {...SourceResponse...} ] } }
Response 404: { "success": false, "error": { "code": "CATEGORY_NOT_FOUND" } }
```

#### GET `/api/v1/categories/{category_id}/subcategories` — 分类下的子分类列表（categories.py:124）

```
Response 200: { "success": true, "data": [ { "tag": "...", "label": "...", "count": 12 } ] }
```

#### PUT `/api/v1/categories/{category_id}` — 更新分类

```
Request: (同 POST, 所有字段可选)
Response 200: { "success": true, "data": { ... updated category ... } }
Response 404: { "success": false, "error": { "code": "CATEGORY_NOT_FOUND" } }
```

#### DELETE `/api/v1/categories/{category_id}` — 删除分类

```
Response 204: No content
Response 404: { "success": false, "error": { "code": "CATEGORY_NOT_FOUND" } }
Note: 删除分类会同时删除关联的数据源 (级联删除)
```

### 3.4 数据源管理 CRUD API

#### GET `/api/v1/sources` — 获取数据源列表

```
Query Params (sources.py:22-33):
  page, page_size (通用分页)
  category_id: uuid (optional) — 按分类筛选
  source_type: str (optional) — "rss" | "api" | "web_scrape" | "social"
  status: str (optional) — 按健康状态筛选: "healthy" | "degraded" | "down" (sources.py:26)
  is_active: bool (optional)

Note: 通用 sort_by/sort_order 未实现（见 §3.1）。

Response 200:
  {
    "success": true,
    "data": [
      {
        "id": "uuid",
        "name": "Yahoo Finance - S&P 500",
        "category_id": "uuid",
        "source_type": "api",
        "url": "https://query1.finance.yahoo.com/v7/finance/...",
        "config": { "api_key_env": "YAHOO_FINANCE_API_KEY", "params": { ... } },
        "refresh_interval_seconds": 60,
        "is_active": true,
        "priority": 5,
        "collector_available": true,
        "health_status": "healthy",
        "last_fetch_at": "2026-06-23T10:00:00Z",
        "last_error": null,
        "created_at": "...",
        "updated_at": "..."
      }
    ],
    "meta": { ... }
  }
```

#### POST `/api/v1/sources` — 创建数据源

```
Request:
  {
    "name": "HackerNews RSS",       // required
    "category_id": "uuid",           // required
    "source_type": "rss",            // required: "rss" | "api" | "web_scrape" | "social"
    "url": "https://hnrss.org/...",  // required
    "config": {                      // optional, 源类型特定配置
      "api_key_env": "HACKERNEWS_API_KEY",
      "headers": { "User-Agent": "..." },
      "parse_rules": { ... }
    },
    "refresh_interval_seconds": 300,  // optional, default from category
    "is_active": true                 // optional, default true
  }

Response 201: { "success": true, "data": { ... created source ... } }
```

#### GET `/api/v1/sources/{source_id}` — 获取单个数据源（sources.py:60）

```
Response 200: { "success": true, "data": { ...SourceResponse... } }
Response 404: { "success": false, "error": { "code": "SOURCE_NOT_FOUND" } }
```

> **SourceResponse 补充字段**: `priority`（1-10，数据源优先级）、`collector_available`
> （bool——无任何采集器可运行于该源时为 false，前端据此解释为何无法启用）。

#### PUT `/api/v1/sources/{source_id}` — 更新数据源

#### DELETE `/api/v1/sources/{source_id}` — 删除数据源

#### GET `/api/v1/sources/{source_id}/health` — 数据源健康状态

```
Response 200:
  {
    "success": true,
    "data": {
      "source_id": "uuid",
      "status": "healthy",           // "healthy" | "degraded" | "down"
      "success_rate_24h": 0.98,
      "avg_response_time_ms": 250,
      "last_success_at": "...",
      "last_failure_at": null,
      "consecutive_failures": 0,
      "total_fetches_24h": 48,
      "last_error": null
    }
  }
```

### 3.5 财经 API

#### GET `/api/v1/finance/search` — 股票/基金搜索

**后端数据源**: 只查本地 `finance_symbols` 表——精确匹配 → 前缀匹配 → 模糊匹配（`ilike`）三级
（`services/finance.py:99-160`）；本地零结果时回退调用 Yahoo Finance 搜索接口补充结果并写回
`finance_symbols` 表。Finnhub **不参与**搜索（仅存在于美股报价采集的 failover 链）。
结果按 `t:{tenant_id}:search:{query_hash}` 缓存 300 秒；`page` / `page_size` 分页在内存结果上执行。

```
Query Params:
  q: str (required) — 搜索关键词（股票代码、名称、基金名称）
  type: str (optional) — "stock" | "fund" | "index" | "commodity" | "all"
  market: str (optional) — "US" | "CN" | "HK" | "JP" | "EU" | "all"
  page: int (default 1)
  page_size: int (default 20)

Response 200:
  {
    "success": true,
    "data": [
      {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "type": "stock",
        "market": "US",
        "exchange": "NASDAQ",
        "current_price": 178.52,
        "change_percent": 1.23,
        "currency": "USD"
      }
    ],
    "meta": { "total": 5, "page": 1, "page_size": 20 }
  }
```

#### GET `/api/v1/finance/quote/{symbol}` — 获取单个行情

```
Query Params:
  detail_level: "basic" | "full" (default "basic")

Response 200:
  {
    "success": true,
    "data": {
      "symbol": "AAPL",
      "name": "Apple Inc.",
      "current_price": 178.52,
      "open": 176.00,
      "high": 180.00,
      "low": 175.50,
      "close_previous": 176.20,
      "volume": 52345678,
      "change": 2.32,
      "change_percent": 1.32,
      "market_cap": 2800000000000,
      "pe_ratio": 28.5,
      "week_high_52": 199.62,
      "week_low_52": 124.17,
      "timestamp": "2026-06-23T10:00:00Z",
      "source": "Yahoo Finance"
    }
  }

Response 404: { "success": false, "error": { "code": "SYMBOL_NOT_FOUND" } }
```

#### GET `/api/v1/finance/market-indices` — 世界主要市场指数

```
Response 200:
  {
    "success": true,
    "data": [
      {
        "symbol": "^GSPC",
        "name": "S&P 500",
        "value": 5234.18,
        "change": 12.34,
        "change_percent": 0.24,
        "market_status": "open",
        "region": "US",
        "timestamp": "..."
      },
      // ... 沪深300, 日经225, 恒生指数, FTSE100, DAX, NASDAQ, etc.
    ]
  }
```

#### GET `/api/v1/finance/commodities` — 黄金/原油/期货

```
Response 200:
  {
    "success": true,
    "data": [
      {
        "symbol": "GC=F",
        "name": "Gold Futures",
        "value": 2345.60,
        "change": 15.30,
        "change_percent": 0.65,
        "unit": "USD/oz",
        "timestamp": "..."
      },
      // ... Crude Oil, Silver, Natural Gas, etc.
    ]
  }
```

#### GET `/api/v1/finance/fund/{symbol}/nav` — 基金NAV估值

```
Query Params:
  estimate_type: "realtime" | "latest_official" (default "realtime")

Response 200:
  {
    "success": true,
    "data": {
      "symbol": "510300",
      "name": "沪深300ETF",
      "nav_official": 4.1234,
      "nav_official_date": "2026-06-22",
      "nav_estimate": 4.1567,
      "nav_estimate_deviation_percent": 0.81,
      "estimate_method": "index_tracking",
      "estimate_timestamp": "2026-06-23T10:30:00Z",
      "underlying_index": {
        "symbol": "000300",
        "name": "沪深300",
        "current_value": 3956.78,
        "change_percent": 0.81
      }
    }
  }
```

#### Watchlist API (见 [finance-tab.md](finance-tab.md) §3.4 详细设计)

```
GET    /api/v1/finance/watchlist               — 获取自选列表
POST   /api/v1/finance/watchlist               — 添加到自选列表 (最多512项, 超出返回400 VALIDATION_ERROR)
DELETE /api/v1/finance/watchlist/{item_id}      — 从自选列表移除
PUT    /api/v1/finance/watchlist/reorder        — 重排序自选列表
GET    /api/v1/finance/watchlist/quotes         — 自选列表所有行情
```

### 3.6 科技资讯 API

#### GET `/api/v1/tech/news` — 科技新闻列表

```
Query Params (api/v1/tech.py:19-31；原文档的 `topic` 参数不存在):
  domain: str (optional) — 按领域筛选
  subcategory: str (optional) — 按子分类筛选
  sort: str (default "hot") — "hot" | "time" | "relevance"
  source_id: uuid (optional)
  since: ISO8601 datetime (optional) — 只返回此时间之后的新闻
  page, page_size (通用分页)

Response 200:
  {
    "success": true,
    "data": [
      {
        "id": "uuid",
        "title": "New AI Model Breaks Record",
        "summary": "...",
        "url": "https://...",
        "source_name": "MIT Tech Review",
        "source_id": "uuid",
        "category_id": "uuid",
        "topic_tags": ["ai", "machine-learning"],
        "published_at": "2026-06-23T08:00:00Z",
        "fetched_at": "2026-06-23T09:00:00Z",
        "image_url": "https://...",
        "priority": 5,
        "domain_tag": "ai",
        "extra_data": { ... },
        "hot_score": 87.5
      }
    ],
    "meta": { "total": 100, "page": 1, "page_size": 20 }
  }
```

#### GET `/api/v1/tech/topics` — 话题标签列表

```
Response 200:
  {
    "success": true,
    "data": [
      { "tag": "ai", "label": "人工智能", "count": 234, "last_active_at": "2026-06-23T09:00:00Z" },
      { "tag": "robotics", "label": "机器人", "count": 56 },
      ...
    ]
  }
```

### 3.7 Dashboard API

> **权限**: 本节全部端点需要 **admin 角色**（`require_admin`），member/viewer 返回 403 `FORBIDDEN`。

#### GET `/api/v1/dashboard/system` — 系统信息

```
Response 200:
  {
    "success": true,
    "data": {
      "version": "1.0.0",
      "uptime_seconds": 86400,
      "environment": "production",
      "python_version": "3.11.5",
      "cpu":     { "cpu_usage_percent": 23.5, "cpu_count": 4 },
      "memory":  { "memory_total_mb": 8192, "memory_used_mb": 2048, "memory_usage_percent": 25.0 },
      "disk":    { "disk_total_gb": 100, "disk_used_gb": 45, "disk_usage_percent": 45.0 },
      "network": { "bytes_sent": 0, "bytes_recv": 0, "packets_sent": 0, "packets_recv": 0 },
      "database": { "postgres_connections": 12, "postgres_active_queries": 1,
                    "redis_connected": true, "redis_memory_used_mb": 128.5 },
      "cpu_count": 4, "cpu_usage_percent": 23.5,
      "memory_total_mb": 8192, "memory_used_mb": 2048,
      "disk_total_gb": 100, "disk_used_gb": 45
    }
    // SystemInfoResponse 为"嵌套分组 (cpu/memory/disk/network/database) + 扁平字段"并存
    // (schemas/dashboard.py:34-49)
  }
```

#### GET `/api/v1/dashboard/services` — 服务健康状态

```
Response 200:
  {
    "success": true,
    "data": [
      {
        "service": "postgresql",
        "status": "healthy",
        "response_time_ms": 5,
        "connection_count": 12,
        "details": { "version": "15.4", "database_size_mb": 500 }
      },
      {
        "service": "redis",
        "status": "healthy",
        "response_time_ms": 1,
        "memory_used_mb": 128,
        "connected_clients": 8
      },
      // ... mongodb (if enabled), celery workers, etc.
    ]
  }
```

#### GET `/api/v1/dashboard/data-sources` — 数据源健康总览

```
Response 200:
  {
    "success": true,
    "data": {
      "total_sources": 25,
      "healthy": 22,
      "degraded": 2,
      "down": 1,
      "sources": [
        { "id": "uuid", "name": "...", "status": "down", "last_error": "...", ... }
      ]
    }
  }
```

#### GET `/api/v1/dashboard/data-sources/{source_id}` — 单数据源健康详情（dashboard.py:64）

```
Response 200: DataSourceHealthDetailResponse —
  { "source_id", "name", "source_type", "status", "success_rate_24h", "avg_response_time_ms",
    "last_success_at", "last_failure_at", "consecutive_failures", "total_fetches_24h",
    "last_error", "health_history": [ ... ], "response_time_trend": [ ... ] }
Response 404: SOURCE_NOT_FOUND
```

#### GET `/api/v1/dashboard/scheduler` — 定时任务状态

```
Response 200:
  {
    "success": true,
    "data": {
      "total_jobs": 12,
      "running_jobs": [ { "job_id": "...", "source_id": "...", "name": "...", "schedule": "...",
                          "original_interval": 60, "current_interval": 120,
                          "adaptive_multiplier": 2.0, "last_run": "...", "next_run": "...",
                          "status": "running", "success_count_24h": 48, "failure_count_24h": 0 } ],
      "paused_jobs": [ ... ],
      "all_jobs": [ ... ],
      "running_jobs_count": 12,
      "paused_jobs_count": 0,
      "last_heartbeat": "2026-08-24T10:00:00+00:00"
    }
    // 开发环境 (内嵌调度器): 列表与计数互为镜像; 生产环境任务在 worker 容器运行,
    // 列表为空, 计数来自 worker 心跳 (scheduler:worker:heartbeat, TTL 45s)
  }
```

#### GET `/api/v1/dashboard/sse-stats` — SSE 连接统计

```
Response 200:
  {
    "success": true,
    "data": {
      "total_connections": 150,
      "connections_by_channel": { "finance": 80, "tech": 40, "dashboard": 30 },
      "peak_connections_24h": 200,
      "peak_connections_today": 120,
      "total_connections_today": 350,
      "total_events_pushed": 50000,
      "average_events_per_minute": 12.5,
      "avg_connection_duration_seconds": 1800.5
    }
    // 注: 字段为 total_events_pushed（累计）, 不存在 events_pushed_24h
  }
```

### 3.8 SSE 推送端点设计

#### GET `/api/v1/stream/{category}` — SSE 实时推送

```
Endpoint: /api/v1/stream/{category}
  category: "finance" | "tech" | "dashboard" | any category slug
Auth: query param token=<jwt_token>

SSE Event Types:
  
  1. item_update — 新信息条目
     event: item_update
     data: {
       "id": "uuid",
       "category_id": "uuid",
       "title": "...",
       "summary": "...",
       "url": "...",
       "source_name": "...",
       "published_at": "...",
       "topic_tags": ["ai"]
     }
  
  2. quote_update — 行情更新 (finance channel)
     event: quote_update
     data: {
       "symbol": "AAPL",
       "current_price": 178.52,
       "change": 2.32,
       "change_percent": 1.32,
       "timestamp": "..."
     }
  
  3. market_index_update — 市场指数更新
     event: market_index_update
     data: { ... market index data ... }
  
  4. nav_estimate_update — 基金估值更新
     event: nav_estimate_update
     data: { ... NAV estimate data ... }
  
  5. commodity_update — 大宗商品更新
     event: commodity_update
     data: { ... commodity data ... }
  
  6. system_metric_update — 系统指标更新 (dashboard channel)
     event: system_metric_update
     data: { "cpu_usage_percent": 23.5, ... }
  
  7. source_health_update — 数据源健康变更 (完整契约见 data-flow.md §3.5.4)
     event: source_health_update
     data: {
       "source_id": "uuid",           // 前端行匹配键
       "name": "Hacker News",
       "source_type": "rss",
       "status": "down",              // healthy | degraded | down
       "previous_status": "degraded",
       "last_error": "connection timeout",
       "last_success_at": "2026-06-23T09:55:00+00:00",
       "last_failure_at": "2026-06-23T10:00:00+00:00",
       "avg_response_time_ms": 432,
       "consecutive_failures": 10,
       "success_count_24h": 40,
       "total_fetches_24h": 48,
       "success_rate_24h": 0.83,
       "timestamp": "2026-06-23T10:00:01+00:00"
     }
  
  8. heartbeat — 心跳
     event: heartbeat
     data: { "timestamp": "2026-06-23T10:00:00Z" }

  9. connected — 连接建立时立即发送的首个事件（sse.py:93-101, SSE 帧 id 固定为 "init"）
     event: connected
     data: {
       "client_id": "{tenant_id}:{user_id}:{uuid}",
       "category": "finance",
       "timestamp": "..."
     }

Client Implementation:
  const es = new EventSource(`/api/v1/stream/finance?token=${jwt}`);
  es.addEventListener('quote_update', (e) => {
    const data = JSON.parse(e.data);
    financeStore.updateQuote(data);
  });
  es.addEventListener('heartbeat', (e) => { /* keep alive */ });
  es.onerror = () => { /* auto reconnect by EventSource */ };
```

**多频道订阅**:
- 客户端可同时建立多个 EventSource 连接 (finance + tech + dashboard)
- 或使用 `/api/v1/stream/all?token=xxx`——路由把路径段字面取值 `all` 作为全频道聚合
  （仅当 `{category}` 恰为 "all" 时连接收到所有频道事件，见 sse_router.py:142-149）

> ⚠️ **未实现**：`channels=finance,tech,dashboard` 多选参数——SSE 路由只接收单一路径段
> `/stream/{category}`，不解析 channels 查询参数；订阅多频道需建立多条连接（或用 `/stream/all`）。

**SSE 补充说明**:
- SSE 响应头硬编码 `Access-Control-Allow-Origin: *`（sse.py:129），绕过 CORS 白名单（见 security.md §3.8 提醒）
- EventRouter 的 Redis 监听还订阅了 `channel:admin` 频道（sse_router.py:160），但目前无任何事件发布者向该频道推送

#### SSE 连接管理端点

```
GET /api/v1/stream/status — 当前用户的 SSE 连接状态
  认证: 可选（get_optional_token, sse.py:25 — token query 参数或 Bearer 头；未携带则返回全空）
  Response: {
    "success": true,
    "data": {
      "active_channels": ["finance", "tech"],
      "connection_id": "{tenant_id}:{user_id}",
      "connected_since": "..."
    }
  }

GET /api/v1/stream/stats — 全局 SSE 统计（无需认证, sse.py:60）
  Response data: { total_connections, connections_by_channel, total_events_pushed,
                   avg_connection_duration_seconds }
```

### 3.9 管理 API (多租户)

```
GET    /api/v1/admin/tenants          — 租户列表 (仅 admin 角色)
POST   /api/v1/admin/tenants          — 创建租户
GET    /api/v1/admin/tenants/{id}     — 租户详情
PUT    /api/v1/admin/tenants/{id}     — 更新租户
DELETE /api/v1/admin/tenants/{id}     — 删除租户
GET    /api/v1/admin/tenants/{id}/stats — 租户统计 (用户数、数据量等)
```

### 3.10 健康检查 API

```
GET /api/v1/health — 服务健康检查 (不需要认证)
  现状: 恒返回 200 { "status": "healthy", "version": "1.0.0", "timestamp": "..." }，
  不做任何依赖检查，也不存在 503 degraded 分支 (health.py:13-19)。
  深度检查请使用下方 /health/detail。

GET /api/v1/health/detail — 依赖深度检查 (不需要认证, health.py:22)
  ⚠️ 安全关注: 公开暴露 PostgreSQL/Redis 状态与运行环境名 (env)，见 §3.1。
  Response 200: {
    "status": "healthy" | "degraded",
    "version": "1.0.0",
    "environment": "production",
    "timestamp": "...",
    "details": {
      "postgresql": { "status": "healthy" | "down", "error": "..." },
      "redis":      { "status": "healthy" | "down", "error": "..." }
    }
  }
  Note: degraded 时仍返回 200（不返回 503）。
```

### 3.11 错误码清单

| 错误码 | HTTP 状态码 | 含义 |
|--------|-----------|------|
| VALIDATION_ERROR | 400 | 请求参数验证失败（含 SSO provider 未启用/不受支持——无独立错误码） |
| INVALID_OAUTH_CODE | 400 | OAuth 授权码无效 |
| NO_COLLECTOR_AVAILABLE | 400 | 激活数据源时无可用采集器（新增） |
| AUTH_REQUIRED | 401 | 需要认证 |
| INVALID_TOKEN | 401 | JWT Token 无效或过期 |
| INVALID_REFRESH_TOKEN | 401 | Refresh Token 无效（含本地管理员登录被禁用后的续期拒绝） |
| INVALID_CREDENTIALS | 401 | 本地管理员登录失败（密码错误/邮箱未知/锁定中，统一文案，新增） |
| SSO_PROVIDER_ERROR | 502 | SSO 提供商返回错误（上游故障；**502 而非 401**，避免前端误跳登录页） |
| FORBIDDEN | 403 | 权限不足 |
| CATEGORY_NOT_FOUND | 404 | 分类不存在 |
| SOURCE_NOT_FOUND | 404 | 数据源不存在 |
| SYMBOL_NOT_FOUND | 404 | 股票/基金代码不存在 |
| ITEM_NOT_FOUND | 404 | 信息条目不存在 |
| DUPLICATE_CATEGORY | 409 | 分类名称已存在 |
| DUPLICATE_WATCHLIST_ITEM | 409 | 自选列表已有该条目 |
| RATE_LIMIT_EXCEEDED | 429 | 请求频率超限（错误码已定义；限流机制本身未实现，见 security.md §3.3） |
| INTERNAL_ERROR | 500 | 服务器内部错误 |
| ADMIN_LOGIN_DISABLED | 503 | 本地管理员登录未启用（新增） |
| SERVICE_UNAVAILABLE | 503 | 服务降级 |

> 修订说明：原文档的 `PROVIDER_NOT_ENABLED` 不存在于代码，实际返回 `VALIDATION_ERROR`；
> `SSO_PROVIDER_ERROR` 的状态码为 502（原误写 401）。

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| SSE 认证方式 | query param token | EventSource API 不支持自定义 header |
| 响应包装 | 统一 success/data/meta/error | 前端处理逻辑一致 |
| API 版本 | /api/v1 | 支持未来版本演进，不破坏现有客户端 |
| 多频道订阅 | 单频道 `/stream/{category}` + 字面 "all" 聚合（`channels` 多选参数未实现，见 §3.8） | 前端可按需求选择，避免过多连接 |
| 分页 | page + page_size | 比 offset/limit 更直观 |

## 5. 边界情况

- **SSE 连接中断**: EventSource 自动重连 (SSE 原生机制)，重连后仅接收新事件
  > ⚠️ **未实现**：`Last-Event-ID` 重连重放——后端不解析 `Last-Event-ID` 请求头（全库零处理），
  > 中断期间错过的事件不会补发；前端重连后应自行通过 REST 拉取对齐。
- **JWT 过期**: SSE 连接中 JWT 过期时，发送 `error` 事件并关闭连接，前端跳转登录
- **并发写入冲突**: 使用 PostgreSQL `ON CONFLICT` 处理分类/数据源名称唯一性
- **搜索空结果**: 返回空数组 + meta.total=0，不返回 404

## 6. 与其他模块的依赖

- → [database.md](database.md): API schema 与数据库模型对应
- → [frontend.md](frontend.md): 前端消费这些 API
- → [security.md](security.md): 认证、限流、CORS 实现
- → [data-flow.md](data-flow.md): SSE 推送的数据流