# 实时推送 (SSE)

**SSE (Server-Sent Events)** 是 InstantBoard 的实时核心，优于 WebSocket 的理由：
- 原生浏览器支持 (EventSource API)
- 自动重连机制
- 纯 HTTP，无需特殊协议升级
- HTTP/2 多路复用

**7 种业务事件**（另有 `heartbeat` 心跳保活）：

| 事件 | 频道 | 触发时机 |
|------|------|---------|
| `quote_update` | finance | REST 请求刷新行情缓存时顺带推送 |
| `market_index_update` | finance | REST 请求刷新指数缓存时顺带推送 |
| `commodity_update` | finance | REST 请求刷新商品缓存时顺带推送 |
| `nav_estimate_update` | finance | REST 请求刷新 NAV 估值缓存时顺带推送 |
| `item_update` | tech | 调度器采集完成时推送（各源 2-5 分钟间隔） |
| `source_health_update` | dashboard | 数据源健康状态变化时实时推送 |
| `system_metric_update` | dashboard | 30 秒采集循环；仅当 CPU/内存数值变化超过阈值 (5%) 时才推送 |

> - 行情类事件（quote/market_index/commodity/nav）**并非调度器周期推送**，而是 REST 请求触发缓存刷新时顺带推送；调度器采集只产生 `item_update`。
> - `system_metric_update` 常被误认为 10 秒推送周期：10 秒实为指标在 Redis 中的缓存 TTL，推送节奏由 30 秒采集循环 + 变化阈值决定。
> - 所有频道均有 30 秒心跳保活，断线后浏览器 EventSource 自动重连。

## 内部实现

事件契约、Redis Pub/Sub 频道与 SSE EventRouter 的内部设计不在本页展开，见 [数据流与 SSE 设计](../dev-guide/design/data-flow.md) §3.5。
