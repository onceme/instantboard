---
version: 1.2
author: designer
date: 2026-08-26
status: draft
cross_refs: [frontend.md, api.md, database.md, data-flow.md, architecture.md, data-sources.md]
---

# InstantBoard Dashboard 模块详细设计

## 目录

1. 目标
2. 方案概述
3. 详细设计
   - 3.1 系统状态指标
   - 3.2 数据采集状态指标
   - 3.3 数据库状态指标
   - 3.4 资源消耗指标
   - 3.5 业务指标
   - 3.6 指标采集方式
   - 3.7 实时更新策略
   - 3.8 图表可视化设计
   - 3.9 资源消耗最小化策略
4. 关键决策
5. 边界情况
6. 与其他模块的依赖

## 1. 目标

定义 InstantBoard Dashboard 监控 Tab 的完整功能设计，包括系统/数据采集/数据库/资源/业务五类指标的定义、采集方式、实时更新策略和可视化方案，要求"尽可能详细又少占资源"。

## 2. 方案概述

提供 **轻量级运维仪表盘**，通过分层指标展示实现信息密度高但资源消耗低，核心策略: psutil轻量采集 + Redis缓存 + SSE增量推送 + 按需深度查询。实际类为 `services/dashboard.py` 的 `DashboardService`（单类，无独立的 SystemMetricsCollector/DatabaseMetricsCollector）。

## 3. 详细设计

### 3.1 系统状态指标

#### 3.1.1 系统信息（现状: `get_system_info()`）

| 指标 | 采集方式 | 展示 |
|------|---------|------|
| 应用版本 version | 配置读取 | 文本 |
| 运行时长 uptime | 进程启动时间差 | 格式化时长 |
| 环境 environment | 配置 | 文本 |
| Python版本 | `platform` | 文本 |
| CPU 使用率 | psutil | 数字 |
| 内存使用率/总量 | psutil | 数字 |
| 磁盘使用率/总量 | psutil | 数字 |
| 数据库连通性 | 会话探测 | 状态 |

> ⚠️ **未实现**：QPS、平均响应时间、错误率 (4xx/5xx)、API 状态灯 — 请求统计中间件只把数据写入 Redis（`SYSTEM_METRICS` / `dashboard:request_metrics:{minute}`），**没有任何端点读取展示**（见 §3.6.2）。

#### 3.1.2 Worker 心跳监控

- `worker_heartbeat_is_fresh`：worker 进程周期性写心跳，api 侧据此判定 **scheduler 服务健康状态**（`/dashboard/services` 的 scheduler 卡片状态依据），心跳过期视为服务降级

### 3.2 数据采集状态指标

#### 3.2.1 各数据源健康状态

**后端字段**（`source_health` 表 + 派生）：status(healthy/degraded/down)、last_success_at、last_failure_at、last_error_message、avg_response_time_ms、consecutive_failures、success_rate_24h、total_fetches_24h。

**DataSourceHealth UI（实际列）**:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
  DSH["DataSourcesHealth — 数据源健康表格"]
  DSH --> Columns["列: 名称 | 类型 | 状态 | 最后成功 | 最后失败 | 响应时间"]
  DSH --> StatusColors["状态色块: healthy / degraded / down"]
  DSH --> Sort["排序: down 优先"]
  DSH --> DownHL["down状态的行: 高亮"]
```

> ⚠️ **未实现**：
> - 「成功率 / 采集频率」列 — 后端已有 `success_rate_24h`/`total_fetches_24h` 但前端未展示（增强项）
> - 过滤器（按类型/分类）与分页
> - 行展开详情 — 展开是空壳，详情数据由 `GET /api/v1/dashboard/data-sources/{source_id}`（含 `health_history`、`response_time_trend`）提供，但该端点从未被前端调用

### 3.3 数据库状态指标（现状）

| 指标 | 采集方式 | 状态 |
|------|---------|------|
| PostgreSQL 连接池 | SQLAlchemy `pool.checkedout()` | ✅ 仅取 checked_out 连接数 |
| PostgreSQL 数据库大小 | `pg_database_size()` | ✅ |
| PostgreSQL 活跃查询数 | `pg_stat_activity` | ✅ |
| Redis 内存使用/最大内存 | `INFO memory` | ✅ used/max 字节 |
| Redis 连接客户端数 | `INFO clients` | ✅ |

> ⚠️ **未实现**：
> - Redis `DBSIZE`（Key 总数）、maxmemory 使用百分比展示
> - MongoDB 监控（项目未启用 MongoDB）
> - PG 连接池详情 `{pool_size, checked_in, overflow}`（实际只取 checked_out）

### 3.4 资源消耗指标（现状）

| 指标 | 采集方式 | 更新频率 | 展示 |
|------|---------|---------|------|
| CPU 使用率 (%) | `psutil.cpu_percent(0.5)` | 30s | 数字 |
| 内存使用率 (%) | `psutil.virtual_memory().percent` | 30s | 数字 |
| 内存使用/总量 (MB) | psutil | 30s | 数字 |
| 磁盘使用/总量 (GB) | `psutil.disk_usage('/')` | 30s | 数字 |
| 网络流量 | `psutil.net_io_counters()` → **累计** `network_bytes_sent/received` | 30s | 数字 |

- **磁盘 I/O**：`psutil.disk_io_counters()` 无任何代码调用，已删除。
> ⚠️ **已知契约冲突（前后端断裂，待修复）**：后端推送的是**累计字节数** `network_bytes_sent/network_bytes_recv`，而前端 `types/index.ts` 期待的是**速率** `network_in_kbps/network_out_kbps` — 后端从不返回速率字段，导致 SystemStatus 网络项恒显示 "--"。

**psutil 采集策略**:
- `cpu_percent(0.5)` 为 0.5s 采样间隔的阻塞调用，经 `asyncio.to_thread` 放入线程池执行，**不阻塞事件循环**（该调用本身约耗 0.5s，并非 "<1ms/次"）
- `virtual_memory()` / `disk_usage()` / `net_io_counters()` 直接读取 /proc，开销可忽略

### 3.5 业务指标

> ⚠️ **未实现**：以下五项业务指标后端全部缺失，无采集无端点。

| 指标 | 状态 |
|------|------|
| 活跃用户数 | ⚠️ 未实现 |
| 今日新增数据条目数 | ⚠️ 未实现 |
| 各分类数据量分布 | ⚠️ 未实现 |
| 自选列表总条目数 | ⚠️ 未实现 |
| SSE推送事件数 (1h) | ⚠️ 未实现 |

### 3.6 指标采集方式汇总

#### 3.6.1 采集架构（现状）

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
  subgraph loop["单个 asyncio 循环任务 (非 APScheduler 分组)"]
    Collect["每 30s: DashboardService.collect_and_push_metrics<br/>(系统/服务/数据源指标采集 + SSE 增量推送)"]
    Archive["每 300s(5分钟): archive_snapshot → PostgreSQL dashboard_snapshots"]
  end

  Collect --> RedisCache["Redis缓存"]
  Collect --> SSEPush["SSE推送 system_metric_update"]
  Archive --> PGArchive["dashboard_snapshots ≈288行/天"]
```

> 说明：原设计的 "Fast 10s / Medium 30s / Slow 5min 三组 APScheduler" 不存在；实际是一个 30s 周期循环内完成所有采集，归档计数器满 300s 时写一条快照。

#### 3.6.2 FastAPI Middleware（现状: `RequestLoggingMiddleware`）

```python
# core/middleware.py — RequestLoggingMiddleware
# 1. 每请求计时，将 QPS/平均响应时间/状态码分布等 **JSON 合并写入**
#    Redis `SYSTEM_METRICS`（dashboard 模块读取的来源之一）
# 2. 每累计 1000 次请求，写一次 `dashboard:request_metrics:{minute}` 字符串
# （注意：这些统计数据目前没有任何 Dashboard 端点读取展示）
```

### 3.7 实时更新策略

#### 3.7.1 SSE 推送策略（现状）

| 指标组 | SSE事件类型 | 推送频率 | 推送条件 |
|--------|-----------|---------|---------|
| 系统指标 | `system_metric_update` | 30s | 与上次比较变化 >5% 的字段才推送 |
| 数据源健康 | `source_health_update` | 实时 | 状态变更时立即推送（payload契约见 [data-flow.md](data-flow.md) §3.5.4） |
| 心跳 | `heartbeat` | 30s | 固定 |

> ⚠️ **未实现**：`db_metric_update`、`business_metric_update` — 后端 `SSEEventType` 仅 8 种事件（item_update/quote_update/market_index_update/nav_estimate_update/commodity_update/system_metric_update/source_health_update/heartbeat），不含这两种。

**增量推送优化**:
- 只推送变化超过 5% 的指标字段，不全量推送
- 前端首次连接通过 REST 获取完整基线，后续接收 SSE 增量
- 增量数据格式: `{metric_name: new_value}`

### 3.8 图表可视化设计

#### 3.8.1 图表选型（现状）

使用 **Chart.js**（经 `vue-chartjs` 封装，前端 `ChartWrapper.vue` 组件）。

| 图表 | 状态 |
|------|------|
| 实时趋势线 (CPU/内存) | ⚠️ 未实现 — store 维护 `cpuHistory/memoryHistory` 数组但无组件渲染；`MetricsChart`/`ResourceUsage` 组件不存在 |
| 状态指示灯 | ✅ HealthPanel 内整体健康状态 |
| 数据表格 | ✅ DataSourcesHealth |

#### 3.8.2 Dashboard 布局（现状: `DashboardView.vue`）

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph LR
  subgraph dashboard["DashboardView 布局"]
    HP["HealthPanel — 顶部全宽: 整体状态灯 + 关键数字摘要"]
    subgraph cols["双列 flex 区域"]
      subgraph left["左列"]
        SS["SystemStatus<br/>版本/运行时长/环境/CPU/内存/磁盘"]
        DSH["DataSourcesHealth<br/>数据源健康表格"]
      end
      subgraph right["右列"]
        SVH["ServicesHealth<br/>PG/Redis/Scheduler/SSE 服务卡片"]
        SSESt["SSEStats<br/>SSE连接统计"]
      end
    end
    HP --> cols
  end
```

- `/dashboard/services` 返回 4 个服务卡片：**postgresql / redis / scheduler / sse**（scheduler 依据 worker 心跳判定）
- SSE 统计字段：活跃连接数、累计连接、`average_events_per_minute`、`avg_connection_duration_seconds`、`peak_connections_today`

> ⚠️ **未实现**：`SchedulerPanel` — `/dashboard/scheduler` 端点与 store 的 fetch 存在，但**无前端组件**渲染任务列表。

#### 3.8.3 图表渲染优化

```javascript
// 策略（设计目标，实际渲染组件待实现）:
// - Chart.js instance 缓存，增量 update('none') 无动画刷新
// - 数据窗口保留最近60个点，旧点自动移除
// - 前端 store 已维护 60 点滚动历史窗口
```

### 3.9 资源消耗最小化策略

#### 3.9.1 采集资源优化

| 策略 | 实现 | 状态 |
|------|------|------|
| **psutil非阻塞** | `asyncio.to_thread(psutil.cpu_percent, 0.5)` | ✅ |
| **复用已有连接** | DB指标用现有SQLAlchemy连接池 | ✅ |
| **Redis INFO单次** | 一次INFO命令获取内存/客户端信息 | ✅ |
| **增量推送** | 仅推送变化>5%字段 | ✅ |
| **归档降频** | dashboard_snapshots 每 5 分钟归档 1 行（≈288 行/天） | ✅ |
| **快照保留** | dashboard_snapshots 超 30 天自动清理（24h 节流，随采集循环执行，见 §3.9.3） | ✅ |

#### 3.9.2 前端资源优化

> ⚠️ **未实现**：虚拟滚动（数据源表格）、`document.hidden` 暂停 SSE/图表（全前端无该逻辑）。图表懒加载已通过路由级代码分割部分达成。

#### 3.9.3 存储优化

- dashboard_snapshots 每 5 分钟归档 1 行 → 每天约 288 行
- 趋势数据不存 PG，仅前端内存/Redis 滚动窗口
- ✅ **已实现**：「超过 30 天的 dashboard_snapshots 自动清理」（`services/dashboard.py` `cleanup_old_snapshots`）：
  piggyback 在指标采集循环（`start_metrics_collection`）中，由内存节流 `snapshot_cleanup_due` 控制——首轮循环立即执行一次，之后每满 24h（`SNAPSHOT_CLEANUP_INTERVAL_SECONDS`）最多执行一次；
  保留窗口 `SNAPSHOT_RETENTION_DAYS = 30`，可用配置 `DASHBOARD_SNAPSHOT_RETENTION_DAYS` 覆盖；
  过滤列为快照的 `timestamp`（该表无 created_at），`DELETE … WHERE timestamp < now() - retention` 并提交，返回删除行数；
  清理失败仅记 warning 日志、不中断指标采集（保留 `last_cleanup_time` 不更新，下一轮重试）。

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 监控方案 | 自建轻量 (psutil+middleware+Redis) | 不引入Prometheus/Grafana，资源消耗极低 |
| 指标采集库 | psutil（经 to_thread 非阻塞） | 无额外进程 |
| 图表库 | Chart.js (vue-chartjs + ChartWrapper) | 轻量 |
| 推送策略 | 增量推送 + REST 基线 | 带宽节省 |
| 归档频率 | 每 5 分钟 1 次（约 288 行/天） | 存储成本可接受 |
| 采集架构 | 单个 asyncio 循环 (30s采集 + 300s归档 + 24h快照清理) | 实现简单，替代原三组 APScheduler 设计 |

## 5. 边界情况

- **psutil 不可用** ✅ 已实现（`services/dashboard.py` 顶部 try/except 导入，缺失时 `psutil=None` + 启动告警日志）：
  `get_system_info` / `collect_and_push_metrics` / `archive_snapshot` 全部判空降级——CPU/内存/磁盘指标为 `None`、网络速率为 0（`sample_network_rates` 不再保留采样状态），快照的 psutil 列存 `None`；DB/Redis/SSE 采集与告警评估照常。响应带 `psutil_available: false` 标记（`SystemInfoResponse` 新增字段），全程不抛异常。
- **阈值告警** ✅ 已实现（`DashboardService._collect_alerts`，每次 30s 采集循环内评估，内存中维护 `[{code, message, triggered_at}]`，同 code 活跃期去重、恢复即移除；`GET /dashboard/system` 新增 `alerts` 字段，列表变化时整体随 `system_metric_update` 推送）：
  - `redis_memory_high`：`used_memory / maxmemory > 0.8`（maxmemory 为 0/未设置则跳过）
  - `db_pool_exhausted`：连接池 `checked_out >= pool_size`（优先 SQLAlchemy `pool.checkedout()/size()`，回退 `status()` 对象取值）
  - `sse_connections_high`：活跃 SSE 连接 > 1000（取自 `event_router.get_stats()` 当前进程注册数；**多进程局限**：`event_router` 是进程内注册表，多 worker 部署下每个 api 进程只看到自己的连接份额）
  - `cpu_high`：CPU 使用率 > 90%（psutil 缺失时跳过）
- **系统负载高自动降频** ✅ 已实现（最小化版本）：CPU 连续 3 次采集 >90%（`HIGH_CPU_STREAK_THRESHOLD`）时采集循环从 30s 放宽到 60s（`get_collect_interval()`），任一采样回落至阈值内即还原 30s；降频期间 `cpu_high` 告警消息附注当前降频间隔。
- **数据源大面积 down**: 表格按 down 优先排序 ✅

## 6. 与其他模块的依赖

- → [api.md](api.md): Dashboard API端点 (`/api/v1/dashboard/*`，含 `data-sources/{source_id}` 详情、`/services`、`/scheduler`、`/sse-stats`)、SSE事件定义
- → [database.md](database.md): source_health表、dashboard_snapshots表、sse_connections表；Redis Key（`SYSTEM_METRICS`、`dashboard:request_metrics:*`）
- → [frontend.md](frontend.md): DashboardView组件层级、布局设计
- → [data-flow.md](data-flow.md): SSE推送机制、Redis Pub/Sub分发
- → [architecture.md](architecture.md): dashboard模块职责划分
- → [data-sources.md](data-sources.md): 数据源健康状态定义、failover设计
