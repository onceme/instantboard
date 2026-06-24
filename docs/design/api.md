---
version: 1.0
author: designer
date: 2026-06-23
status: draft
cross_refs: [architecture.md, database.md, frontend.md, security.md, data-flow.md]
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

**通用分页参数** (所有列表端点):
- `page`: int, default 1, min 1
- `page_size`: int, default 20, min 1, max 100
- `sort_by`: str, default "created_at"
- `sort_order`: "asc" | "desc", default "desc"

**认证**: 除 SSE 端点外，所有 `/api/v1/*` 端点需要 JWT Token (Bearer Auth)
- Header: `Authorization: Bearer <jwt_token>`
- SSE 端点: 通过 query param `token=<jwt_token>` 认证 (EventSource 不支持自定义 header)

**多租户**: 所有请求自动注入 `tenant_id` (从 JWT claims 中提取)

### 3.2 认证与授权 API

#### POST `/api/v1/auth/sso/{provider}` — SSO 登录

**provider**: `google` | `azure_ad` | `github` | `apple` | `facebook`

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
  { "success": false, "error": { "code": "INVALID_OAUTH_CODE", ... } }

Response 401:
  { "success": false, "error": { "code": "SSO_PROVIDER_ERROR", ... } }
```

#### POST `/api/v1/auth/refresh` — 刷新 Token

```
Request:
  { "refresh_token": "eyJ..." }

Response 200:
  { "success": true, "data": { "access_token": "eyJ...", "expires_in": 3600 } }

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
Response 200:
  { "success": true, "data": { "message": "Logged out" } }
```

### 3.3 分类管理 CRUD API

#### GET `/api/v1/categories` — 获取分类列表

```
Query Params:
  page: int (default 1)
  page_size: int (default 20)
  include_sources: bool (default false) — 是否包含关联数据源
  type: str (optional) — 按分类类型筛选: "finance" | "tech" | "news" | "custom"

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

#### GET `/api/v1/categories/{category_id}` — 获取单个分类

```
Response 200: { "success": true, "data": { ... category with sources ... } }
Response 404: { "success": false, "error": { "code": "CATEGORY_NOT_FOUND" } }
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
Query Params:
  page, page_size, sort_by, sort_order (通用分页)
  category_id: uuid (optional) — 按分类筛选
  source_type: str (optional) — "rss" | "api" | "web_scrape" | "social"
  is_active: bool (optional)

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

**后端数据源**: 优先使用 Finnhub Symbol Lookup API (`/search?q=XXX`), 回退到 yfinance 本地搜索

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
      "52_week_high": 199.62,
      "52_week_low": 124.17,
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
Query Params:
  topic: str (optional) — "robotics" | "ai" | "embedded" | "space" | "all"
  source_id: uuid (optional)
  page, page_size (通用分页)
  since: ISO8601 datetime (optional) — 只返回此时间之后的新闻

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
        "priority": 5
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
      { "tag": "ai", "label": "人工智能", "count": 234 },
      { "tag": "robotics", "label": "机器人", "count": 56 },
      ...
    ]
  }
```

### 3.7 Dashboard API

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
      "cpu_count": 4,
      "cpu_usage_percent": 23.5,
      "memory_total_mb": 8192,
      "memory_used_mb": 2048,
      "disk_total_gb": 100,
      "disk_used_gb": 45
    }
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

#### GET `/api/v1/dashboard/scheduler` — 定时任务状态

```
Response 200:
  {
    "success": true,
    "data": [
      {
        "job_id": "finance_market_indices",
        "name": "Market Indices Fetch",
        "schedule": "every 30s",
        "last_run": "...",
        "next_run": "...",
        "status": "active",
        "success_count_24h": 48,
        "failure_count_24h": 0
      }
    ]
  }
```

#### GET `/api/v1/dashboard/sse-stats` — SSE 连接统计

```
Response 200:
  {
    "success": true,
    "data": {
      "total_connections": 150,
      "connections_by_channel": {
        "finance": 80,
        "tech": 40,
        "dashboard": 30
      },
      "peak_connections_24h": 200,
      "events_pushed_24h": 5000
    }
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
  
  7. source_health_update — 数据源健康变更
     event: source_health_update
     data: { "source_id": "uuid", "status": "down", ... }
  
  8. heartbeat — 心跳
     event: heartbeat
     data: { "timestamp": "2026-06-23T10:00:00Z" }

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
- 或使用 `/api/v1/stream/all?token=xxx&channels=finance,tech,dashboard` 统一订阅

#### SSE 连接管理端点

```
GET /api/v1/stream/status — 当前用户的 SSE 连接状态
  Response: {
    "success": true,
    "data": {
      "active_channels": ["finance", "tech"],
      "connection_id": "uuid",
      "connected_since": "..."
    }
  }
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
  Response 200: { "status": "healthy", "version": "1.0.0", "timestamp": "..." }
  Response 503: { "status": "degraded", "details": { "postgresql": "down" } }
```

### 3.11 错误码清单

| 错误码 | HTTP 状态码 | 含义 |
|--------|-----------|------|
| VALIDATION_ERROR | 400 | 请求参数验证失败 |
| INVALID_OAUTH_CODE | 400 | OAuth 授权码无效 |
| AUTH_REQUIRED | 401 | 需要认证 |
| INVALID_TOKEN | 401 | JWT Token 无效或过期 |
| INVALID_REFRESH_TOKEN | 401 | Refresh Token 无效 |
| SSO_PROVIDER_ERROR | 401 | SSO 提供商返回错误 |
| FORBIDDEN | 403 | 权限不足 |
| CATEGORY_NOT_FOUND | 404 | 分类不存在 |
| SOURCE_NOT_FOUND | 404 | 数据源不存在 |
| SYMBOL_NOT_FOUND | 404 | 股票/基金代码不存在 |
| ITEM_NOT_FOUND | 404 | 信息条目不存在 |
| DUPLICATE_CATEGORY | 409 | 分类名称已存在 |
| DUPLICATE_WATCHLIST_ITEM | 409 | 自选列表已有该条目 |
| RATE_LIMIT_EXCEEDED | 429 | 请求频率超限 |
| INTERNAL_ERROR | 500 | 服务器内部错误 |
| SERVICE_UNAVAILABLE | 503 | 服务降级 |

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| SSE 认证方式 | query param token | EventSource API 不支持自定义 header |
| 响应包装 | 统一 success/data/meta/error | 前端处理逻辑一致 |
| API 版本 | /api/v1 | 支持未来版本演进，不破坏现有客户端 |
| 多频道订阅 | 支持单频道 + all 统一订阅 | 前端可按需求选择，避免过多连接 |
| 分页 | page + page_size | 比 offset/limit 更直观 |

## 5. 边界情况

- **SSE 连接中断**: EventSource 自动重连 (SSE 原生机制)，后端需处理重连后 `Last-Event-ID` 头
- **JWT 过期**: SSE 连接中 JWT 过期时，发送 `error` 事件并关闭连接，前端跳转登录
- **并发写入冲突**: 使用 PostgreSQL `ON CONFLICT` 处理分类/数据源名称唯一性
- **搜索空结果**: 返回空数组 + meta.total=0，不返回 404

## 6. 与其他模块的依赖

- → [database.md](database.md): API schema 与数据库模型对应
- → [frontend.md](frontend.md): 前端消费这些 API
- → [security.md](security.md): 认证、限流、CORS 实现
- → [data-flow.md](data-flow.md): SSE 推送的数据流