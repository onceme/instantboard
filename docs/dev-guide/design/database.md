---
version: 1.4
author: designer
date: 2026-08-27
status: revised
cross_refs: [architecture.md, api.md, security.md, data-flow.md, admin-login.md, finance-tab.md]
---

# InstantBoard 数据库设计

## 1. 目标

定义 InstantBoard 的完整数据库方案：PostgreSQL 表结构、Redis 使用场景、MongoDB 后续版本按需启用场景、数据迁移策略和多租户隔离方案。

## 2. 方案概述

**PostgreSQL 存核心关系数据，Redis 存缓存/实时/会话数据，MongoDB 初始版本不启用（后续版本按需启用）**，初始版本通过 PostgreSQL JSONB 替代 MongoDB 的灵活存储场景。

## 3. 详细设计

### 3.1 PostgreSQL 表结构

> 下表结构已与代码 models 逐项核对（12 张表全部吻合）；轻微出入：若干标注为 `DESC` 的
> 索引在代码中实际为升序创建，不影响功能。以下定义以实际代码为准逐步对齐。

```sql
-- ============================================
-- 租户与用户
-- ============================================

CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    slug            VARCHAR(50) NOT NULL UNIQUE,
    plan            VARCHAR(20) NOT NULL DEFAULT 'free'  -- 'free'|'pro'|'enterprise'
                    CHECK (plan IN ('free', 'pro', 'enterprise')),
    settings        JSONB NOT NULL DEFAULT '{}',           -- 租户级配置 (刷新频率覆盖等)
    max_users       INTEGER NOT NULL DEFAULT 5,
    max_categories  INTEGER NOT NULL DEFAULT 10,
    max_sources     INTEGER NOT NULL DEFAULT 50,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL,
    name            VARCHAR(100) NOT NULL,
    avatar_url      TEXT,
    sso_provider    VARCHAR(20) NOT NULL  -- 'google'|'azure_ad'|'github'|'apple'|'facebook'|'local'
                    CHECK (sso_provider IN ('google', 'azure_ad', 'github', 'apple', 'facebook', 'local')),
    sso_provider_id VARCHAR(255) NOT NULL,  -- SSO 提供商的用户ID（本地管理员为 'local:{email}'）
    role            VARCHAR(20) NOT NULL DEFAULT 'member'  -- 'admin'|'member'|'viewer'
                    CHECK (role IN ('admin', 'member', 'viewer')),
    preferences     JSONB NOT NULL DEFAULT '{}',           -- 用户偏好; 现由 /users/me/preferences 端点管理
                                                           -- {"favorite_tags": ["llm", ...]} (科技二级标签,
                                                           -- 科技频道 relevance 排序加权, 见 tech-tab.md §3.5.1)
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE (tenant_id, email),
    UNIQUE (sso_provider, sso_provider_id)
);

CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_users_sso ON users(sso_provider, sso_provider_id);

-- 注意: sso_provider 的 CHECK 约束 (chk_users_sso_provider) 实际为 6 值:
-- 5 个 SSO 提供商 (google, azure_ad, github, apple, facebook) + 'local'
-- (本地管理员登录引入, models/user.py:34-35, 见 admin-login.md)。
-- 应用层通过 ENABLED_SSO_PROVIDERS 环境变量控制哪些 SSO 提供商可被使用,
-- 数据库约束保留全部值以确保向后兼容。

-- 注意: preferences 列在全新库上由 baseline 迁移 (versions/bb1a61d49502)
-- 或 create_all 建出；引入该列之前建库的存量库仍需手工补齐:
--   ALTER TABLE users ADD COLUMN preferences jsonb NOT NULL DEFAULT '{}';
-- 补齐全库schema与baseline一致后 `alembic stamp bb1a61d49502` 接入迁移
-- 链路（见 §3.4）。

-- ============================================
-- 分类与数据源
-- ============================================

CREATE TABLE categories (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                VARCHAR(50) NOT NULL,
    slug                VARCHAR(50) NOT NULL,
    description         VARCHAR(200),
    icon                VARCHAR(50) DEFAULT 'folder',      -- 图标名称
    color               VARCHAR(7) DEFAULT '#3B82F6',       -- hex color
    type                VARCHAR(20) NOT NULL  -- 'finance'|'tech'|'news'|'custom'
                        CHECK (type IN ('finance', 'tech', 'news', 'custom')),
    refresh_interval_seconds INTEGER NOT NULL DEFAULT 300,  -- 刷新频率(秒)
    keywords_filter     JSONB DEFAULT '[]',                  -- 关键词过滤列表
    priority_sort       BOOLEAN NOT NULL DEFAULT false,      -- 是否按优先级排序
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE (tenant_id, slug)
);

CREATE INDEX idx_categories_tenant ON categories(tenant_id);
CREATE INDEX idx_categories_type ON categories(tenant_id, type);

CREATE TABLE sources (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    category_id             UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name                    VARCHAR(100) NOT NULL,
    source_type             VARCHAR(20) NOT NULL  -- 'rss'|'api'|'web_scrape'|'social'
                            CHECK (source_type IN ('rss', 'api', 'web_scrape', 'social')),
    url                     TEXT NOT NULL,
    config                  JSONB NOT NULL DEFAULT '{}',     -- 源类型特定配置
    refresh_interval_seconds INTEGER,                         -- null = 使用分类默认值
    is_active               BOOLEAN NOT NULL DEFAULT true,
    priority                INTEGER NOT NULL DEFAULT 5,      -- 1(高) - 10(低)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sources_category ON sources(category_id);
CREATE INDEX idx_sources_tenant ON sources(tenant_id);
CREATE INDEX idx_sources_type ON sources(tenant_id, source_type);

-- ============================================
-- 数据源健康监控
-- ============================================

CREATE TABLE source_health (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status              VARCHAR(10) NOT NULL DEFAULT 'healthy'  -- 'healthy'|'degraded'|'down'
                        CHECK (status IN ('healthy', 'degraded', 'down')),
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    last_error_message  TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_fetches_24h   INTEGER NOT NULL DEFAULT 0,
    success_count_24h   INTEGER NOT NULL DEFAULT 0,
    avg_response_time_ms INTEGER NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE (source_id)
);

CREATE INDEX idx_source_health_status ON source_health(status);

-- ============================================
-- 信息条目 (所有分类的通用内容模型)
-- ============================================

CREATE TABLE items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    category_id     UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    source_id       UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    title           VARCHAR(500) NOT NULL,
    summary         TEXT,
    url             TEXT NOT NULL,
    image_url       TEXT,
    published_at    TIMESTAMPTZ NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic_tags      JSONB DEFAULT '[]',       -- 话题标签数组
    extra_data      JSONB DEFAULT '{}',       -- 扩展数据 (财经行情、科技元数据等)
    priority        INTEGER NOT NULL DEFAULT 5,
    is_processed    BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- 去重: 同一 URL + 发布时间 不重复
    UNIQUE (tenant_id, source_id, url, published_at)
);

CREATE INDEX idx_items_category ON items(category_id, published_at DESC);
CREATE INDEX idx_items_tenant_time ON items(tenant_id, published_at DESC);
CREATE INDEX idx_items_tags ON items USING gin(topic_tags);
CREATE INDEX idx_items_extra_data ON items USING gin(extra_data);
CREATE INDEX idx_items_source ON items(source_id);

-- ============================================
-- 财经专用表
-- ============================================

CREATE TABLE finance_symbols (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    symbol          VARCHAR(20) NOT NULL,          -- 股票/基金代码
    name            VARCHAR(200) NOT NULL,
    type            VARCHAR(20) NOT NULL  -- 'stock'|'fund'|'index'|'commodity'|'futures'|'currency'
                    CHECK (type IN ('stock', 'fund', 'index', 'commodity', 'futures', 'currency')),
    market          VARCHAR(10) NOT NULL,          -- 'US'|'CN'|'HK'|'JP'|'EU'|'GLOBAL'
    exchange        VARCHAR(50),
    currency        VARCHAR(3) DEFAULT 'USD',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE (tenant_id, symbol)
);

CREATE INDEX idx_finance_symbols_type ON finance_symbols(tenant_id, type);
CREATE INDEX idx_finance_symbols_market ON finance_symbols(tenant_id, market);

CREATE TABLE finance_quotes (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    symbol_id       UUID NOT NULL REFERENCES finance_symbols(id) ON DELETE CASCADE,
    current_price   DECIMAL(18,4),
    open_price      DECIMAL(18,4),
    high_price      DECIMAL(18,4),
    low_price       DECIMAL(18,4),
    close_previous  DECIMAL(18,4),
    volume          BIGINT,
    change_value    DECIMAL(18,4),
    change_percent  DECIMAL(8,4),
    market_cap      BIGINT,
    pe_ratio        DECIMAL(8,2),
    52_week_high    DECIMAL(18,4),
    52_week_low     DECIMAL(18,4),
    timestamp       TIMESTAMPTZ NOT NULL,
    source_name     VARCHAR(50),                  -- 数据来源名称
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (id, timestamp)   -- PG 要求分区键包含在所有唯一约束中，见下方分区说明
) PARTITION BY RANGE (timestamp);

CREATE INDEX idx_finance_quotes_symbol_time ON finance_quotes(symbol_id, timestamp DESC);
CREATE INDEX idx_finance_quotes_tenant ON finance_quotes(tenant_id);

-- 分区 ✅ 已实现（按月 RANGE 分区）: models/finance.py 声明
-- postgresql_partition_by = "RANGE (timestamp)"，主键因此为复合键 (id, timestamp) ——
-- PostgreSQL "分区键必须包含在所有唯一约束中" 的硬性要求；ORM 恒等查询 (session.get)
-- 需带完整复合键（当前代码仅写入行情行、不按主键查询，无影响）。SQLite 忽略该方言
-- 选项，建为普通表。
--
-- 分区供给 (app/db/partitions.py::ensure_quote_partitions，PG-only，幂等):
--  - 新库建表后供给上月/当月/下月分区。两条建表路径都会调用:
--    create_tables 路径在 create_all 后立即调用; Alembic 迁移路径由
--    entrypoint.sh 在 upgrade head 成功后调用（baseline 迁移只建分区父表,
--    月分区是运行时状态, 不写死进迁移文件）。命名
--    finance_quotes_y{yyyy}m{mm}，范围半开 [UTC 月初, 下次月初)；
--  - 并发启动竞争（check 与 CREATE 之间被抢先建出）按 42P07 (duplicate_table) 容错；
--  - finance_quotes 已存在为普通表（存量库）时建表无法修改既有表，记警告跳过，
--    需按文末手工迁移指引操作；无表 / 非 PG 方言直接跳过。
--  - 实现细节: asyncpg 不支持 DDL 绑定参数，分区边界以 ISO-8601 字面量写入；
--    pg_class.relkind 在 asyncpg 下解码为 bytes，查询需 ::text。
--
-- 月度滚动: 调度器每日 00:30 UTC cron finance_quotes_partition_roll
-- (scheduler/manager.py::add_quote_partition_job，main.py lifespan 与 worker.py
-- 双接线)，重查上月/当月/下月覆盖，保证跨月前下月分区就位；失败仅记日志不杀任务。
-- 无默认分区：分区未覆盖的时间戳写入会报错，这正是每日滚动任务要防止的失效模式。
--
-- 存量普通表手工迁移指引（PG 不支持原地 ALTER TABLE ... PARTITION BY，需重建表；
-- 以下要点已在 PostgreSQL 15 实测）:
--   -- 1) 建分区新表 (LIKE INCLUDING DEFAULTS 保留 NOT NULL 与默认值；
--   --    PG 的 LIKE 不把主键/外键带到分区表，主键须含分区键)
--   CREATE TABLE finance_quotes_new (LIKE finance_quotes INCLUDING DEFAULTS)
--       PARTITION BY RANGE (timestamp);
--   ALTER TABLE finance_quotes_new ADD PRIMARY KEY (id, timestamp);
--   ALTER TABLE finance_quotes_new
--       ADD CONSTRAINT fk_fq_new_tenant  FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
--       ADD CONSTRAINT fk_fq_new_symbol FOREIGN KEY (symbol_id) REFERENCES finance_symbols(id) ON DELETE CASCADE;
--   -- 2) 建覆盖历史数据范围的分区 (命名与 ensure_quote_partitions 一致，缺哪个历史月补哪个)
--   CREATE TABLE finance_quotes_new_y2026m08 PARTITION OF finance_quotes_new
--       FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
--   -- 3) 迁移数据 (超出已建分区范围的行会报错)
--   INSERT INTO finance_quotes_new SELECT * FROM finance_quotes;
--   -- 4) rename 切换 (低峰分步执行)。注意: 表改名不会重命名索引——旧表占用的
--   --    idx_finance_quotes_* 索引名需先改名 (*_old) 或新索引先起别名再统一改回
--   ALTER TABLE finance_quotes RENAME TO finance_quotes_old;
--   ALTER TABLE finance_quotes_new RENAME TO finance_quotes;
--   DROP TABLE finance_quotes_old;  -- 验证后再删
--   -- 子表名切换后保留 finance_quotes_new_ 前缀（不影响路由），可统一 rename 为
--   -- finance_quotes_y{yyyy}m{mm}；切换完成后应用侧 ensure_quote_partitions 接管
--   -- 后续的按月分区供给。

CREATE TABLE fund_nav_estimates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    symbol_id           UUID NOT NULL REFERENCES finance_symbols(id) ON DELETE CASCADE,
    nav_official        DECIMAL(18,4),             -- 最新官方NAV (每日20:00由 fund_nav_official_refresh 写入)
    nav_official_date   DATE,                      -- 官方NAV日期
    nav_estimate        DECIMAL(18,4),             -- 实时估值 (get_fund_nav 估值成功后回写)
    nav_estimate_deviation_percent DECIMAL(8,4),   -- 估值偏差百分比
    estimate_method     VARCHAR(50),                -- 行类型/估值方法: 'official'(每日官方净值行) / 'index_tracking'(实时估值行, 按 基金+官方净值日期 upsert)
    estimate_timestamp  TIMESTAMPTZ NOT NULL,
    underlying_index_symbol VARCHAR(20),            -- 跟踪指数代码
    underlying_index_value  DECIMAL(18,4),          -- 指数当前值
    underlying_index_change_percent DECIMAL(8,4),   -- 指数涨跌幅
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fund_nav_symbol ON fund_nav_estimates(symbol_id, estimate_timestamp DESC);

-- ============================================
-- 自选关注列表
-- ============================================

CREATE TABLE watchlist_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol_id       UUID NOT NULL REFERENCES finance_symbols(id) ON DELETE CASCADE,
    display_order   INTEGER NOT NULL DEFAULT 0,      -- 排序位置
    notes           VARCHAR(200),                     -- 用户备注
    alert_threshold_percent DECIMAL(8,4),            -- 涨跌提醒阈值
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE (user_id, symbol_id)
);

CREATE INDEX idx_watchlist_user ON watchlist_items(user_id, display_order);

-- ============================================
-- SSE 连接记录 (审计/统计)
-- ============================================

CREATE TABLE sse_connections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channels        JSONB NOT NULL DEFAULT '[]',      -- 订阅频道列表
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    disconnected_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    client_ip       VARCHAR(45),                     -- IPv4/IPv6
    user_agent      TEXT
);

CREATE INDEX idx_sse_connections_user ON sse_connections(user_id);
CREATE INDEX idx_sse_connections_active ON sse_connections(disconnected_at) WHERE disconnected_at IS NULL;

-- ============================================
-- Dashboard 指标快照 (每小时归档)
-- ============================================

CREATE TABLE dashboard_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cpu_usage_percent   DECIMAL(5,2),
    memory_used_mb      INTEGER,
    memory_total_mb     INTEGER,
    disk_used_gb        DECIMAL(8,2),
    disk_total_gb       DECIMAL(8,2),
    active_sse_connections INTEGER,
    events_pushed_hour   INTEGER,
    healthy_sources      INTEGER,
    degraded_sources     INTEGER,
    down_sources         INTEGER,
    scheduler_jobs_active INTEGER,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_dashboard_snapshots_time ON dashboard_snapshots(tenant_id, timestamp DESC);
```

### 3.2 Redis 使用场景

| 场景 | Key 模式 | Value 类型 | TTL | 说明 |
|------|---------|-----------|-----|------|
| **用户会话** | `session:{session_id}` | String (JSON) | 24h | JSON 字符串 `{user_id, tenant_id, provider}`；**不含 refresh token**（services/auth.py:106-113），登出时按 user_id 扫描清除 |
| **行情实时缓存** | `t:{tenant_id}:quote:{symbol}` | String (JSON) | 30s | 当前价格、涨跌幅、时间戳 |
| **市场指数缓存** | `t:{tenant_id}:market_indices` | String (JSON) | 60s | 所有市场指数汇总 |
| **大宗商品缓存** | `t:{tenant_id}:commodities` | String (JSON) | 60s | 黄金、原油等 |
| **基金NAV缓存** | `t:{tenant_id}:nav:{symbol}` | String (JSON) | 120s | 估值数据 |
| **SSE Pub/Sub** | `channel:{category}` | Pub/Sub | 无 | 数据更新事件分发（finance/tech/dashboard/admin/all） |
| **搜索缓存** | `t:{tenant_id}:search:{query_hash}` | String (JSON) | 300s | 金融搜索结果缓存（query_hash = md5(q:type:market)） |
| **去重集合** | `t:{tenant_id}:dedup:{source_id}` | Set | ⚠️ **无 TTL（永不过期）** | 成员为 `MD5(title:url)` 十六进制摘要（processors/dedup.py:41-43），快速去重 |
| **数据源健康缓存** | `source_health:{source_id}` | ⚠️ 混用两种格式 | 300s / 无 | 采集路径写 JSON 字符串 `ex=300`（collectors/base.py:186）；创建数据源时写 Hash 且**无 TTL**（services/source.py:277-285）——格式不一致，待统一修复 |
| **自选列表缓存** | `t:{tenant_id}:watchlist:{user_id}` | String (JSON) | 600s | `get_watchlist` 响应数组的读写缓存：命中直接返回，未命中查库后写入；加自选/删自选/重排/阈值 PATCH 四条变异路径均 `redis_delete` 即时失效；脏条目（非 JSON/非 list）删除自愈；Redis 不可用 → 读写均降级直查 PG，变异照常提交（services/finance.py，finance-tab.md §3.2） |
| **系统指标缓存** | `dashboard:system_metrics` | Hash | 10s | 实时系统指标 |
| **请求指标（累计）** | `dashboard:api_metrics:totals` | Hash | ⚠️ 无 TTL | RequestLoggingMiddleware 每请求 `HINCRBY requests_total 1`；`GET /dashboard/system` `api` 分组读取（跨 api 重启不归零） |
| **请求指标（分钟桶）** | `dashboard:api_metrics:minute:{minute}` | Hash | 120s | 字段 `count` / `latency_ms`(响应时间累计) / `count_2xx` / `count_4xx` / `count_5xx`；中间件每请求单条 pipeline 原子递增（跨多 worker 精确），读侧按滑动 60s 窗口合并上一分钟+当前分钟计算 QPS/平均响应/错误率（core/middleware.py + services/dashboard.py） |
| **调度器心跳** | `scheduler:worker:heartbeat` | String | 45s | worker 每 15s 续期（3× 间隔）；API 侧以此为新鲜度阈值判断 worker 健康 |
| **SSE 活跃连接（负载信号）** | `sse:active_connections` | String（整数计数） | 60s | api 进程在 SSE `register`/`unregister` 时以 SET 全量计数写入（自校正，无 INCR/DECR 漂移）并由 30s 心跳续期（`core/sse_router.py`）；调度侧 `scheduler/manager.py::evaluate_load_multiplier` 每轮采集读取作负载降频（连接数 >500 → ×2，见 finance-tab.md §3.8.3）。api 进程挂掉后键过期，调度侧回落正常频率（失败开放） |
| **话题统计推送节流** | `tech:topic_stats_pushed:{tenant_id}` | String（标记位 `"1"`） | 900s | `topic_stats_update` SSE 推送节流（`services/sse.py::publish_topic_stats_update`）：科技条目入库触发时以 `SET NX EX 900` 原子抢占，窗口内同租户至多推送一次；统计加载失败时删除该键释放窗口以便重试（tech-tab.md §3.8） |
| **自选涨跌提醒冷却** | `finance:alert_fired:{tenant_id}:{item_id}` | String（标记位 `"1"`） | 3600s | 自选涨跌提醒触发冷却（`services/finance.py::_check_alert_threshold`）：自选行情链路检测到 `\|涨跌幅\| >= 条目阈值` 时以 `SET NX EX 3600` 原子抢占，窗口内同条目至多推送一次 `alert_update`；Redis 不可用时跳过检测（不报错、行情照常返回）（finance-tab.md §3.2） |
| **Token 黑名单** | `token_blacklist:{jti}` | String | token 剩余有效期 | access/refresh 撤销（core/security.py:75） |
| **管理员防爆破** | `admin_login:fail:{email}` / `lock:{email}` | String（计数/锁） | 15min | email 维度：5 次失败触发锁定（详见 admin-login.md §7） |
| **管理员防爆破 (IP)** | `admin_login:fail_ip:{ip}` / `lock_ip:{ip}` | String（计数/锁） | 1h | IP 维度：20 次失败触发锁定 |
| **限流计数** | `rate:{tenant_id}:{ip}:{endpoint}` | — | — | 见下方未实现标注 |
| **IP 黑名单** | `ip_blacklist` | — | — | 见下方未实现标注 |
| **SSO State** | `sso_state:{state_key}` | — | — | 见下方未实现标注 |

> ⚠️ **未实现（限流）**：`rate:{tenant_id}:{ip}:{endpoint}` 键与 `rate_limit_key()` helper
> 存在定义，但全代码库零调用，**应用层限流完全未落地**（见 security.md §3.3）。
>
> ⚠️ **未实现（IP 黑名单）**：`ip_blacklist` 仅有 `RedisKeys.IP_BLACKLIST` 定义，零使用点，
> 无封禁与检查逻辑。
>
> ⚠️ **未实现（SSO state 存储）**：`sso_state:{state_key}` 从未写入——authorize 端点生成
> state 后直接返回，不存不校验（api/v1/auth.py:62），OAuth CSRF 防护缺失。
>
> ⚠️ **说明（key 前缀）**：并非所有 key 都有租户前缀——仅租户级数据键（quote、market_indices、
> commodities、nav、search、dedup、watchlist）带 `t:{tenant_id}:` 前缀；session、source_health、
> token_blacklist、admin_login:*、dashboard:*、scheduler:*、channel:*、sse:* 等均为全局键。
> 特例：话题统计推送节流键 `tech:topic_stats_pushed:{tenant_id}` 与涨跌提醒冷却键
> `finance:alert_fired:{tenant_id}:{item_id}` 为租户级但采用后缀式命名。

**Redis 配置要点**:
- `maxmemory-policy: allkeys-lru` — 内存满时淘汰最久未使用的 key
- `maxmemory: 512mb` (开发/生产统一)
- `appendonly: no` — 默认关闭 AOF，仅 RDB 快照持久化
- `requirepass` — 生产环境密码认证

### 3.3 MongoDB 使用场景

**初始版本不启用 MongoDB**。以下场景标注为后续版本按需启用，初始版本通过 PostgreSQL JSONB 字段替代。

**何时使用 MongoDB**: 仅在以下场景按需启用（初始版本默认不启动，生产环境通过 `--profile mongodb` 按需启动，后续版本可启用）

| 场景 | Collection | 理由 | 初始版本替代方案 |
|------|-----------|------|----------------|
| **原始抓取内容** | `raw_crawled_content` | RSS原文、网页HTML，schema不统一 | PostgreSQL items.extra_data JSONB 字段存储 |
| **历史行情数据** | `historical_quotes` | 日级/分钟级历史数据量大 | PostgreSQL finance_quotes 分区表 (近期数据) + 归档策略 |
| **新闻全文** | `news_full_text` | 部分源提供全文，内容长度不一 | PostgreSQL items.summary 字段 (截断200字符) + items.extra_data JSONB |

**Collection 定义**:

```javascript
// raw_crawled_content
{
  _id: ObjectId,
  tenant_id: UUID,
  source_id: UUID,
  raw_html: String,         // 原始HTML
  raw_json: Object,         // 原始JSON (API源)
  fetched_at: ISODate,
  processed: Boolean,
  processing_errors: Array
}

// historical_quotes (时序集合)
{
  _id: ObjectId,
  tenant_id: UUID,
  symbol: String,
  timestamp: ISODate,
  open: Number,
  high: Number,
  low: Number,
  close: Number,
  volume: Number
}
// 创建时序集合:
// db.createCollection("historical_quotes", { timeseries: { timeField: "timestamp", metaField: "symbol" } })

// news_full_text
{
  _id: ObjectId,
  tenant_id: UUID,
  item_id: UUID,             // 关联 PostgreSQL items 表
  full_text: String,
  images: Array,
  metadata: Object,
  fetched_at: ISODate
}
```

**MongoDB 不存储的数据**:
- 用户/租户/权限 → PostgreSQL (关系完整性)
- 分类/数据源配置 → PostgreSQL (结构化配置)
- 最新行情 → Redis (高频实时)
- 自选列表 → PostgreSQL (持久化) + Redis (缓存)

### 3.4 数据迁移策略

**工具**: Alembic (SQLAlchemy 生态标准) — ✅ **已实现**（2026-08-27，baseline + upgrade-first 启动）

**迁移体系**:

- 目录: `app/alembic/` = `alembic.ini` + `env.py` + `script.py.mako` + `versions/`
- `env.py`: 异步引擎（asyncpg + `connection.run_sync` + `asyncio.run`），
  `target_metadata = Base.metadata`，`compare_type=True`；**必须 import `app.models`
  包**（只 import `app.models.base` 时 metadata 为空，autogenerate 检测不到任何表）。
  数据库 URL 运行时取自 `settings.database_url`（覆盖 ini 中的占位值），因此
  `alembic -c app/alembic/alembic.ini ...` 始终作用于当前环境配置的库
- Baseline 迁移 `versions/bb1a61d49502_baseline_schema.py` = 当前 12 张表全集
  （tenants/users/categories/sources/source_health/items/finance_symbols/
  fund_nav_estimates/watchlist_items/finance_quotes/dashboard_snapshots/
  sse_connections）。在干净 PG 库上 `revision --autogenerate` 生成后人工复核，
  `alembic upgrade head` 应用，并与 `create_all` 建出的 schema 做了
  `pg_dump` 对比——逐字一致（仅多 `alembic_version` 表）。`downgrade()` 为
  逆序 drop 全部表

**启动链路（upgrade-first）**: `entrypoint.sh` 检测 `versions/` 是否含迁移脚本：

- 有迁移 → `alembic upgrade head`（失败回退 `create_tables()`）；upgrade 成功后
  补一次 `ensure_quote_partitions()` 供给月分区——迁移只建分区父表
  （`PARTITION BY RANGE (timestamp)`），具体月分区是运行时状态（见 §3.1）
- 无迁移（versions 为空）→ 保持旧行为，回退 `init_db.create_tables()`
  （`Base.metadata.create_all` + 分区供给）
- `main.py` lifespan 与 worker 启动的 `create_tables()` 调用保留：`create_all`
  checkfirst 跳过已存在表、`ensure_quote_partitions` 幂等，两条路径互为兜底

**新增迁移流程**: `make makemigration msg="描述"`（= `alembic -c
app/alembic/alembic.ini revision --autogenerate -m "..."`）→ **人工审查** →
`make migrate`（= `upgrade head`）。autogenerate 盲区（本次已逐项核对，以后每次
复核时对照）：

- 分区: `postgresql_partition_by` 与复合主键 `(id, timestamp)` 本次被捕获，但
  **月度分区 DDL 永远不会**出现在迁移里——分区供给始终走
  `ensure_quote_partitions` + 每日 00:30 UTC 滚动任务（时间边界不可写死进迁移）
- CHECK 约束、JSONB `server_default`（`'{}'::jsonb` 等）、部分索引
  （`postgresql_where`）、GIN 索引、`52_week_high` 这类需引号的列名——本次均被
  捕获，但 autogenerate 对 server_default 的方言差异并不可靠，逐表对照模型审查
- 存量库接入: 已由 `create_all` 建库且 schema 与 baseline 等价的，执行
  `alembic stamp bb1a61d49502` 标记已迁移，后续增量迁移在其上执行
- 无损迁移（加列/加表）可在线执行；有损迁移多步走（加新列 → 数据迁移 → 删旧列）
- MongoDB 不涉及迁移工具（schema-free，直接修改应用代码）

**历史风险（已收敛）**: `create_tables()` 不修改已存在的表——引入迁移体系前，
模型变更在存量库上不会自动生效（如 admin-login.md §6 的 CHECK 约束手工变更）。
现在模型变更一律走"改模型 → `make makemigration` → 审查 → `make migrate`"；
`create_tables()` 仅作无迁移时的兜底，不再承担 schema 演进。

### 3.5 多租户数据隔离方案

**方案**: **共享数据库 + 行级隔离 (Shared DB, Shared Schema, Row-level Isolation)**

**实现（现状）**:
- 所有业务表包含 `tenant_id` 列 (NOT NULL, FK → tenants)
- 隔离在**端点依赖注入层**完成：`get_current_tenant`（`app/dependencies.py:57-63`）从
  JWT claims 解析 tenant_id，每个端点显式接收 `tenant_id: str = Depends(get_current_tenant)`，
  并将其显式传给 service 层，由各查询语句携带 `tenant_id ==` 条件过滤
- **不存在**统一的"自动注入 `WHERE tenant_id`"机制——每条查询需手工携带租户条件，
  靠代码评审与测试防止遗漏

> ⚠️ **未实现（TenantMiddleware）**：早期设计为 FastAPI 中间件 `TenantMiddleware` 提取
> tenant_id 并注入 SQLAlchemy session context；代码中不存在此中间件（`setup_middlewares`
> 仅注册 CORS + `RequestLoggingMiddleware`），实际采用上述依赖注入方案。

> ⚠️ **未实现（RLS）**：PostgreSQL Row Level Security **完全未启用**——全代码库无
> `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY` / `current_setting('app.current_tenant_id')`；
> `docker/postgres/init.sql:26-27` 注释称 "RLS 将由后续 Alembic 迁移创建"，该迁移并不存在
> （见 §3.4）。**当前租户隔离纯靠应用层代码。** 以下 RLS 设计保留为规划。

<details><summary>规划中的 RLS 设计（未实现）</summary>

```sql
-- 启用 RLS (PostgreSQL 特性)
ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance_symbols ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance_quotes ENABLE ROW LEVEL SECURITY;

-- 创建策略: 仅允许访问自己租户的数据
CREATE POLICY tenant_isolation ON categories
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

</details>

**Redis 隔离**: 仅租户级数据键带 `t:{tenant_id}:` 前缀（见 §3.2 说明）
**MongoDB 隔离** (后续版本启用时): 所有查询包含 `tenant_id` 条件

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 多租户隔离 | 行级隔离 + RLS（**RLS 未实现**，当前纯应用层隔离，见 §3.5） | 共享数据库成本最低、运维简单 |
| 历史行情存储 | PostgreSQL 近期 (初始版本) | 初始版本仅用PostgreSQL分区存储近期行情，历史数据归档策略后续优化；MongoDB时序存储为后续版本可选增强 |
| 去重策略 | PostgreSQL UNIQUE + Redis Set | 双重保障：PG持久化去重、Redis快速去重 |
| 是否分区 | finance_quotes 按月 RANGE(timestamp) 分区 ✅ 已实现（新库经 baseline 迁移或 `create_tables` 建分区父表，`ensure_quote_partitions` 供给上月/当月/下月分区；调度每日 00:30 UTC 滚动补下月；存量普通表需手工重建，见 §3.1） | 行情数据量大，分区查询性能好 |
| 连接池 | SQLAlchemy async session + pool | FastAPI async 需要异步连接池 |

## 5. 边界情况

- **数据库连接失败**: FastAPI lifespan 中检测连接，失败时返回 503
- **Redis 不可用**: 降级为直接 PostgreSQL 查询，SSE 降级为 REST 拉取
- **MongoDB 不可用**: 初始版本不启用MongoDB，原始内容存储使用PostgreSQL JSONB字段 (items.extra_data)；后续版本启用MongoDB时，MongoDB不可用降级为PostgreSQL JSONB字段
- **迁移冲突**: 迁移体系已建立（见 §3.4），启动走 upgrade-first。多实例并发启动都执行
  `alembic upgrade head`：先完成者落库（PG 事务性 DDL），后者冲突失败时经
  `entrypoint.sh` 回退 `create_tables()`（IF NOT EXISTS 语义，schema 等价）；
  `alembic_version` 表保证已执行的迁移不会重放。生产建议滚动发布时先让一个实例
  完成 migrate 再扩容
- **大表查询**: items 表可能百万级，依赖索引 + 分页，不使用全量查询

## 6. 与其他模块的依赖

- → [api.md](api.md): API schema 与数据库模型映射
- → [security.md](security.md): 多租户隔离安全、RLS配置
- → [data-flow.md](data-flow.md): 数据存储流程、去重逻辑
- → [data-sources.md](data-sources.md): 数据源配置决定抓取频率和数据格式