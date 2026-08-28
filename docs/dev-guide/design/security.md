---
version: 1.3
author: designer
date: 2026-08-28
status: revised
cross_refs: [architecture.md, api.md, database.md, infrastructure.md, admin-login.md]
---

# InstantBoard 安全设计

## 1. 目标

定义 InstantBoard 的完整安全方案，包括注入防护、DDoS防护、SSO集成、多租户隔离安全、JWT管理、HTTPS和CORS，确保系统安全可靠。

## 2. 方案概述

采用 **多层防御 (Defense in Depth)** 策略：Nginx层限流/SSL → FastAPI中间件层认证/隔离 → 数据层RLS/参数化查询，5种SSO通过统一OAuth2流程集成。

> 📌 **现状提示（2026-08-24 审计修订，2026-08-27 RLS 落地，2026-08-28 CSP 与 Origin 校验落地，2026-08-28 L4 请求验证落地，2026-08-28 L3 IP 黑名单手工封禁落地，2026-08-28 L2 应用层限流落地）**：本文档为"设计 + 现状"混合文档——尚未落地的防护层
> （Nginx 限流等）
> 均已在对应小节加 `⚠️ 未实现` 标注，规划内容保留作为路线图；
> 代码已实现但此前未记录的机制统一补充在 §3.9。数据层 RLS 已实现（见 §3.5），
> CSP 与 Origin/Referer 校验已实现（见 §3.2），L4 请求验证（User-Agent + 请求体大小）已实现（见 §3.3 层级 4），
> L3 IP 黑名单的手工管理（管理员端点 + 最外层中间件拦截）已实现（见 §3.3 层级 3）；自动封禁仍未实现。
> L2 应用层限流（Redis ZSET 滑动窗口，租户+IP+路由分级）已实现（见 §3.3 层级 2）；
> 租户级阈值覆写与 SSE 每用户连接数上限仍是规划。

## 3. 详细设计

### 3.1 SQL 注入防护方案

| 层级 | 方案 | 实现 |
|------|------|------|
| ORM层 | **全部使用 SQLAlchemy ORM** | 不手写SQL，所有查询通过 ORM 生成参数化查询 |
| 原始SQL | **严格禁止** | 如需原始SQL（仅迁移脚本），使用 `text()` + 参数绑定 |
| 输入验证 | **Pydantic schema** | 所有API输入经过Pydantic验证，类型+长度+格式约束 |
| 特殊字符 | **输入清洗** | 用户输入的搜索关键词，转义 `%` 和 `_` (LIKE操作) |

> ⚠️ **未实现**：LIKE 通配符转义——`services/finance.py:130-135` 的金融搜索将用户输入直接拼入
> `ilike(f"%{q}%")`，未转义 `%`/`_`（不构成 SQL 注入，但通配符会放宽匹配范围，与上表规划不符）。

**关键规则**:
- 禁止字符串拼接SQL
- 禁止 `f-string` 构建SQL
- 所有动态查询参数通过 SQLAlchemy `filter()` / `params()` 传入
- Alembic 迁移中的原始SQL使用 `text("... :param").bindparams(param=value)`

### 3.2 XSS/CSRF 防护方案

#### XSS 防护

| 方案 | 实现 |
|------|------|
| 输出编码 | Vue 3 默认转义 HTML ({{ }})，不使用 v-html (除非 sanitized) |
| 输入过滤 | 用户输入 (搜索、备注) 存入数据库前不做HTML过滤，输出时转义 |
| CSP Header | ✅ **已实现**：两个服务器模板（`docker/nginx/conf.d/http-server.conf.template`、`https-server.conf.template`）的 Security headers 段硬编码下发（`always`，错误响应同样生效，模板内注释说明差异理由）：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'`。相对原目标策略的扩展：`img-src` 增加 `data:`（新闻缩略图/内联图场景）；`base-uri`/`frame-ancestors`/`object-src` 为纵深加固（`frame-ancestors 'none'` 在现代浏览器中取代 `X-Frame-Options DENY`）。兼容性依据：前端为同源 SPA（脚本/样式全自托管、构建产物无内联脚本），API 与 EventSource SSE 均同源（`connect-src 'self'` 覆盖），无第三方脚本/字体/样式引入。**调优方式**：策略为模板硬编码常量（非 envsubst 变量，无需环境变量），修改模板后 `docker compose ... up -d --force-recreate nginx` 重部署即生效。回归护栏：`backend/tests/unit/test_nginx_security_headers.py`（静态断言两个模板的 CSP 关键指令与既有四个安全头均在） |
| DOMPurify | 当前**无需**：前端未安装 DOMPurify 依赖，且全前端无任何 `v-html` 使用，渲染全部走 Vue 默认转义 |
| Token 存储 | JWT 不入 Cookie，后端**不设置任何 Cookie**（全代码库无 `set_cookie`/`Set-Cookie`）；access/refresh 两个 token 存前端 `localStorage`，纯 Bearer/JSON body 传递，风险评估见 §3.6 |

#### CSRF 防护

| 方案 | 实现 |
|------|------|
| 纯 Bearer token 认证 | 认证不使用 Cookie（后端无 `Set-Cookie`，浏览器不会自动附带任何认证 Cookie）→ CSRF 自然免疫 |
| SSO OAuth State | ✅ **已实现**：authorize 端点生成的 `state`（`secrets.token_urlsafe(32)`）写入 `sso_state:{state}`（value=provider 名，TTL 10 分钟）；`POST /auth/sso/{provider}` 在 code 换取之前校验——必填、键存在、provider 匹配，通过后**立即删键（一次性）**；缺失/未知/过期/已用/不匹配统一 400 `VALIDATION_ERROR`（消息固定为 "Invalid or expired OAuth state"，不泄露具体原因）。前端另在客户端比对 sessionStorage 中的 state（`composables/useAuth.ts`）。Redis 不可用时 authorize 与校验两端均 **fail-open**（warning 日志）——登录可用性优先：攻击仍需诱导受害者完成完整 OAuth 流程、且恰逢该环境 Redis 故障，风险窗口可接受（见 §5） |
| SSE Token | SSE 认证用 query param `token` — 不受 CSRF 影响 |
| Origin验证 | ✅ **已实现**：`OriginGuardMiddleware`（core/middleware.py），`setup_middlewares` 在 CORS 之后注册（运行栈位于 `RequestLoggingMiddleware` 之内、CORS 之外，403 仍被请求统计记录）。仅校验**状态变更方法**（POST/PUT/PATCH/DELETE）；GET/HEAD/OPTIONS 放行（SSE 走 GET、preflight 由 CORS 处理）。判定：请求带 `Origin` 头 → 与 `settings.cors_origins`（与 CORS 共用同一允许列表，两层永不分歧）**精确匹配（含端口）**；无 `Origin` → 回退比对 `Referer` 的 `scheme://host[:port]` 前缀；**两者皆无 → 放行**——非浏览器客户端（curl / 服务端互调 / TestClient）合法地不携带这两类头，而浏览器发起的跨站状态变更请求必带其一，故该取舍不误伤任何合法写入。允许源集合含 `*` 时全放行（与 CORS 通配语义一致；**生产环境勿用通配**，否则本层形同虚设）。不匹配 → 403 `FORBIDDEN`（"Origin not allowed"，标准错误信封）；解析/配置异常 → 放行 + warning 日志（可用性优先）。**防御定位**：纯 Bearer 无 Cookie 认证本已对 CSRF 免疫，本层为纵深防御——拦截携带合法 Bearer token 的跨站伪造请求（如被盗 token 从恶意页面重放） |

### 3.3 DDoS 防护方案

#### 速率限制 (多层级)

**层级 1: Nginx 限流 (粗粒度)**

> ⚠️ **未启用**：`docker/nginx/nginx.conf:46-48` 中以下三条 `limit_req_zone` 全部处于注释状态，
> 所有 location 均未配置 `limit_req`——Nginx 层限流目前仅是规划。

```nginx
# nginx.prod.conf
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/s;
limit_req_zone $binary_remote_addr zone=sse_limit:10m rate=5r/s;
limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=3r/s;

location /api/v1/auth/ {
    limit_req zone=auth_limit burst=5 nodelay;
    ...
}
location /api/v1/stream/ {
    limit_req zone=sse_limit burst=3 nodelay;
    ...
}
location /api/v1/ {
    limit_req zone=api_limit burst=20 nodelay;
    ...
}
```

**层级 2: FastAPI 限流 (细粒度，租户级)**

- ✅ **已实现（Redis ZSET 滑动窗口）**：`RateLimitMiddleware`（core/middleware.py）替换原空壳直通：
  - **算法**：每个限流桶是一个 Redis ZSET。每请求先记录本次时间戳（`ZADD key now "{now}:{uuid 后缀}"`，
    uuid 后缀防止同毫秒并发请求塌缩成同一成员），再裁剪滑出窗口的成员
    （`ZREMRANGEBYSCORE key 0 (now-60s)`，-exclusive 上界使恰好一个窗口老的成员多存活一轮），
    以 `ZCARD` 判定：窗口内请求数（含本次）> 阈值 → 429；最后 `EXPIRE key 60+5s`
    让空闲桶在成员全部滑出后随即回收。四条命令聚合为**单条非事务 pipeline = 单往返**
    （`redis_sliding_window_count`，core/redis.py）——位于每请求热路径，往返数最小化。
    窗口固定 60s（`RATE_LIMIT_WINDOW_SECONDS`，所有限流档位均以"每分钟"定义）。
  - **键结构**：`rate:{tenant_id|anon}:{ip}:{route_class}`（复用既有 `RedisKeys.rate_limit_key()`）。
    tenant 从 Authorization Bearer 或 SSE `token` query param **尽力不验签解码**（复用
    `extract_tenant_from_token_unverified`，与 get_db 的 RLS 上下文同一信任模型）；
    无 token / 解码失败 / 非 UUID 租户声明 → 共享 `anon` 桶（非法声明不得污染键布局）。
    ip 用 `get_client_ip` 右起解析（与 L3 黑名单、管理员防爆破同口径）。
    ⚠️ 已知残余：攻击者可伪造 UUID 租户声明把流量摊到多个桶——该取舍与 RLS 上下文一致，
    剩余风险由 IP 维度、auth 层防爆破锁定与 L1/L3 兜底约束。
  - **路由分级（`_route_class`）**：
    | 档位 | 路径 | 阈值（每分钟） | 说明 |
    |------|------|--------------|------|
    | auth | `/api/v1/auth/*` | `RATE_LIMIT_AUTH_PER_MINUTE` 默认 **10** | 管理员登录另有 email+IP 双维度防爆破锁定叠加，此档仅需温和值即可压制凭据填充体量 |
    | search | `/api/v1/finance/search`、`/api/v1/tech/search` | `RATE_LIMIT_SEARCH_PER_MINUTE` 默认 **20** | 交互式搜索，收紧 |
    | default | 其余 `/api/v1/*` | `RATE_LIMIT_PER_MINUTE` 默认 **60** | 常规 API |
  - **burst 语义**：窗口内实际容忍 `档位阈值 + RATE_LIMIT_BURST`（默认 10）次请求——
    为页面加载扇出等短促合法尖峰留出余量，避免误伤。
  - **豁免**：`/api/v1/health*`（CD 冒烟/手工排查）、`/api/v1/stream*`（SSE 长连接，
    流式响应会长期占用"一次请求"而毒化计数；SSE 连接另行治理）、非 `/api/v1` 路径
    （语义对齐 `RequestLoggingMiddleware.SKIP_PATHS`）。
  - **超限响应**：429 + 标准错误信封（`RATE_LIMIT_EXCEEDED`，"Rate limit exceeded"）
    + `Retry-After: 60`。信封由中间件**直接构造**（不走异常处理器——中间件位于
    FastAPI exception handlers 之外，抛 `HTTPException` 会变成 500；与栈内其他中间件同模式）。
    被拒请求同样计入窗口（攻击持续时窗口保持满载）。
  - **开关与降级**：`RATE_LIMIT_ENABLED`（默认 true）关闭时整体直通；
    **fail-open**——Redis 任何异常放行 + debug 日志（与 OriginGuard/RequestValidation/IP 黑名单
    的可用性优先口径一致）。
  - **栈位置**：`IPBlacklist → RequestLogging → RateLimit → OriginGuard → RequestValidation → CORS → router`。
    位于 IPBlacklist 之内：被封禁 IP 先吃 403，不消耗限流预算、不产生 rate 键；
    位于 RequestLogging 之内：被限流的 429 仍被日志与请求统计记录
    （dashboard `error_rate_4xx` 即攻击可观测信号）；
    位于 OriginGuard/RequestValidation 之外：畸形与跨站请求同样消耗预算
    （吸收垃圾流量正是本层职责）。
  - 回归护栏：`tests/unit/test_rate_limit.py`（分级/键构成/边界/豁免/开关/降级/滑窗恢复）、
    `tests/integration/test_rate_limit.py`（全栈 429 信封、租户与 IP 桶隔离）。
- ⚠️ **仍是规划**：原草图中"每个租户独立的限流配置"（按租户覆写阈值）与
  "SSE 每用户 3 连接"上限均未实现——当前阈值为全局配置；`RateLimitExceeded` 异常
  类保留但不由中间件抛出（429 信封由中间件直接构造，原因见上）。

**层级 3: IP 黑名单**

- ✅ **已实现（手工封禁 + 请求层拦截）**：
  - **存储**：`RedisKeys.IP_BLACKLIST = "ip_blacklist"`（Redis Set，永久成员、无 TTL），成员为规范化后的
    IPv4/IPv6 字符串（`ipaddress` 压缩形式，`0:0:0:0:0:0:0:1` 与 `::1` 视为同一封禁）。
  - **管理端点**（`api/v1/admin.py`，均需 `require_admin`，响应 `SuccessResponse` 包装）：
    `GET /admin/security/ip-blacklist`（按字典序列出）、
    `POST /admin/security/ip-blacklist` body `{ip}`（`ipaddress.ip_address` 校验，非法 → 400 `VALIDATION_ERROR`；
    SADD 幂等，重复添加成功且只留一条）、
    `DELETE /admin/security/ip-blacklist/{ip}`（SREM **幂等成功**语义——删除不存在的 IP 也返回成功，
    管理员意图是"确保该 IP 未被封禁"，重试/并发删除保持安全；未选 404）。
  - **拦截**：`IPBlacklistMiddleware`（core/middleware.py）注册为**最外层**中间件——运行栈
    `IPBlacklist → RequestLogging → OriginGuard → RequestValidation → CORS → router`，
    被封禁 IP 的请求在日志/请求统计/源校验/路由之前即被丢弃，连统计都不进。
    命中 → 403 `FORBIDDEN`，固定简洁信封 "Access denied"（不泄露黑名单存在性细节）。
  - **IP 判定防伪**：复用 `dependencies.get_client_ip` 的右起解析——取 `X-Forwarded-For` 自右向左第一个
    合法 IP（可信代理追加的真实对端），与管理员登录防爆破同口径；攻击者伪造左段无法绕过封禁，
    也无法用被禁 IP 栽赃右段的真实对端。
  - **进程内缓存**：每请求查 Redis 有一次 RTT 开销，改为 `SMEMBERS` 整集拉取后内存判存——
    快照缓存 + 短 TTL（`IP_BLACKLIST_CACHE_TTL`，默认 30s，可配）；管理端点每次增删后调用
    `invalidate_ipblacklist_cache()` 使**本进程**即时生效（多 api worker 时其他进程在 TTL 窗口内收敛）。
  - **fail-open**：缓存构造失败 / Redis 不可用 → 放行 + debug 日志（可用性优先，与 OriginGuard/
    RequestValidation 口径一致）；失败的拉取不污染缓存，下次请求重试。
  - 回归护栏：`tests/unit/test_ip_blacklist.py`、`tests/integration/test_ip_blacklist.py`。
- ⚠️ **未实现（自动封禁，后续增强）**：原规划"连续触发限流 5 次 → 自动加黑 1h"依赖在 L2 应用层限流之上
  叠加**攻击计数器**（持续超限的 (IP) 升级封禁）；L2 滑动窗口本身已实现（见上方层级 2），
  但该升级逻辑尚未落地，当前仅管理员手工封禁。

**层级 4: 请求验证**

- ✅ **已实现**：验证 `User-Agent` 存在且非空（阻止简单脚本）——`RequestValidationMiddleware`（core/middleware.py）：`/api/v1/*` 请求 UA 缺失或纯空白 → 400 `VALIDATION_ERROR`（"User-Agent header is required"）。开关 `REQUIRE_USER_AGENT`（默认 true）。**豁免**：`/api/v1/health` 前缀（CD 冒烟/手工 curl 排查，前缀匹配覆盖 `/health/detail`，语义对齐 `RequestLoggingMiddleware.SKIP_PATHS`）；nginx 容器探活路径 `/healthz` 由 nginx 直接 `return 200 "ok"`、不代理到 FastAPI，不在 `/api/v1/` 前缀下**天然豁免**。浏览器 SSE（EventSource）请求自带 UA，不受影响
- ✅ **已实现**：验证请求大小 < 10KB（阻止超大请求）——同一中间件：`Content-Length` 超过 `MAX_REQUEST_BODY_BYTES`（默认 10240）的请求 → 413（标准错误信封，code `VALIDATION_ERROR`，消息 "Request body must not exceed {limit} bytes"）。**无 Content-Length 的 chunked/流式请求不拦**——提前拦截需缓冲请求体，本层仅做廉价预过滤，真正的请求体上限职责归 nginx / L2 限流。解析异常（如非法 Content-Length 值）放行 + warning 日志（可用性优先，与 OriginGuard 口径一致）
- ℹ️ **无独立实现**：API 端点 JWT 格式预检——不设独立中间件；格式/签名/有效期校验由依赖注入的 JWT 完整解析（`Depends(get_current_tenant)`）+ refresh token 黑名单校验承担（见 §3.6），畸形 token 统一 401 `INVALID_TOKEN`，语义等价于规划的"预检"
- ✅ 已实现：SSE 端点强制 `token` query param（`Query(...)` 必填，缺失返回 422）

**栈位置**：`setup_middlewares` 注册顺序使运行栈为 `IPBlacklist → RequestLogging → RateLimit → OriginGuard → RequestValidation → CORS → router`——被封禁 IP 最先被拒（403 "Access denied"，不进日志与统计，也不消耗限流预算）；其后是 L2 限流，位于 `RequestLogging` 之内，超限 429 仍被日志与请求统计记录（`error_rate_4xx` 即攻击可观测信号）；再其后先拒跨站（OriginGuard 403）再拒畸形（本层 400/413）——这两类拒绝也发生在限流之内，畸形与跨站流量同样消耗限流预算。回归护栏：`tests/unit/test_core_middleware.py::TestRequestValidation*`、`tests/integration/test_request_validation.py`、`tests/unit/test_rate_limit.py`、`tests/integration/test_rate_limit.py`。

**DDoS 防护层级总结**:

| 层级 | 技术 | 保护对象 | 限制粒度 |
|------|------|---------|---------|
| L1: Nginx | limit_req | 全局 | IP级 |
| L2: FastAPI | ✅ Redis ZSET 滑动窗口 | 租户级 | （租户+IP+路由档位）级 |
| L3: 黑名单 | Redis Set + 中间件 | 恶意IP | IP级 |
| L4: 请求验证 | Header检查 | 异常请求 | 请求级 |

> **现状总结**：L1 未启用、L2 已实现（Redis ZSET 滑动窗口，分级阈值 + burst + 豁免 + fail-open，
> 见层级 2）、L3 手工封禁已实现（管理员端点 + 最外层中间件拦截；自动封禁仍待"持续超限→升级封禁"
> 的攻击计数器逻辑，见层级 3 标注）、L4 已大部分落地（UA/请求体大小验证、SSE token 必填，
> JWT 校验由依赖注入承担）。当前实际生效的防滥用机制是
> 本地管理员登录的 email + IP 双维度防爆破锁定（见 [admin-login.md](admin-login.md) §7）、
> L2 应用层限流、L4 请求验证中间件与 L3 IP 黑名单（手工）。L1 与 L3 自动封禁的分层设计保留为实施路线图。

### 3.4 SSO 集成设计 (5种提供商，默认启用 Google + GitHub)

#### 提供商启用/禁用机制

InstantBoard 支持 5 种 SSO 提供商，但**默认只启用 Google 和 GitHub**。

**配置驱动**：通过环境变量 `ENABLED_SSO_PROVIDERS`（逗号分隔）控制启用的提供商列表。

```python
# config.py
ENABLED_SSO_PROVIDERS: list[str] = ["google", "github"]  # 默认值

# 应用启动时读取环境变量：
# ENABLED_SSO_PROVIDERS=google,github            → 仅启用 Google 和 GitHub
# ENABLED_SSO_PROVIDERS=google,github,azure_ad    → 额外启用 Azure AD
# ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook → 全部启用
```

**启用检查**：

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph LR
    A["前端加载"] -->|"GET /api/v1/auth/sso/providers"| B["后端返回<br/>enabled_providers"]
    B --> C["前端动态渲染<br/>只显示启用的登录按钮"]
    D["用户点击登录"] -->|"POST /api/v1/auth/sso/{provider}"| E{"provider 在<br/>ENABLED_SSO_PROVIDERS 中?"}
    E -->|"否"| F["返回 400<br/>VALIDATION_ERROR"]
    E -->|"是"| G["执行 OAuth2 流程"]
```

**设计原则**：
- **代码保留**：所有 5 种提供商的实现代码均保留，不删除任何 handler
- **配置驱动**：仅通过 `ENABLED_SSO_PROVIDERS` 环境变量控制启用/禁用
- **向后兼容**：数据库 CHECK 约束包含所有 5 个提供商值，应用层单独控制启用逻辑
- **错误码**：未启用/不受支持的提供商返回 `VALIDATION_ERROR (400)`（与通用参数校验共用错误码，无独立码）

#### 统一OAuth2 流程架构

```mermaid
%%{init: {"theme": "base", "themeVariables": {"actorBkg": "#ffffff", "actorBorder": "#767676", "actorTextColor": "#000000", "actorLineColor": "#767676", "noteBkgColor": "#ffffff", "noteTextColor": "#000000", "noteBorderColor": "#767676", "activationBkgColor": "#ffffff", "activationBorderColor": "#767676", "signalColor": "#767676", "signalTextColor": "#767676", "labelBoxBkgColor": "#ffffff", "labelBoxBorderColor": "#767676", "labelTextColor": "#000000", "loopTextColor": "#767676", "altSectionBkgColor": "#ffffff", "sequenceNumberColor": "#000000", "fontSize": "14px"}, "sequence": {"mirrorActors": true, "actorMargin": 50, "width": 160, "height": 50, "messageMargin": 40, "noteMargin": 10, "boxMargin": 8, "wrap": true}}}%%
sequenceDiagram
    participant FE as Frontend
    participant BE as Backend
    participant SSO as SSO Provider
    FE->>BE: 0. GET /auth/sso/providers
    BE-->>FE: 返回 enabled_providers
    FE->>FE: 1. 动态渲染登录按钮
    FE->>BE: 2. GET /auth/sso/{provider}/authorize
    BE->>BE: 2a. 校验 provider 已启用; 存 state 入 Redis (TTL 10min)
    BE-->>FE: 返回授权 URL + state
    FE->>SSO: 3. 重定向授权页
    SSO-->>FE: 4. 用户授权后回调
    FE->>BE: 5. POST code + state
    BE->>BE: 5a. 校验 state (存在+provider匹配) 并删键 (一次性)
    BE->>SSO: 6a. code 换 access_token
    SSO-->>BE: 6b. 返回 access_token
    BE->>BE: 6c. 取用户信息, 建/查用户, 签发 JWT
    BE-->>FE: 7. 返回 access + refresh token
    Note over BE: 各提供商差异仅在 Step 6
```

#### Google OAuth2

```python
# app/core/sso_handlers.py — GoogleSSOHandler (:87)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"

# 特殊要点:
# - 必须在 Google Cloud Console 创建 OAuth 2.0 Client
# - 支持 HD 参数限制组织域名 (企业租户)
# - Token 包含 email_verified 字段，必须验证 —— ✅ 已实现：`sso_login` 对 provider='google'
#   强制校验——email_verified 显式为 False 时拒绝登录（400 VALIDATION_ERROR，
#   消息 "Google account email is not verified"，在查库/建用户之前拦截）；字段缺失
#   (None) 按放行处理（向后兼容）；非 Google 提供商不受影响
#   （app/services/auth.py sso_login、app/core/sso_handlers.py GoogleSSOHandler，见 §3.4 说明）
# - Refresh token 仅在首次授权时返回
```

#### Microsoft Azure AD

```python
# app/core/sso_handlers.py — AzureADSSOHandler (:141)
AUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
USERINFO_URL = "https://graph.microsoft.com/v1.0/me"  # 实际代码取值 (sso_handlers.py:144)
SCOPE = "openid email profile User.Read"

# 特殊要点:
# - tenant_id 区分: common (多租户) / organizations (仅组织) / consumers (仅个人)
# - id_token 是 JWT，可直接解析用户信息 (减少一次网络请求)
# - 支持 conditional access policies (条件访问策略)
# - 组织管理员可限制仅公司域名登录
```

#### GitHub OAuth

```python
# app/core/sso_handlers.py — GitHubSSOHandler (:203)
AUTH_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USERINFO_URL = "https://api.github.com/user"
EMAIL_URL = "https://api.github.com/user/emails"  # email需要单独获取
SCOPE = "user:email read:user"

# 特殊要点:
# - email 不在主 userinfo 中，需要额外请求 /user/emails
# - 需要筛选 primary + verified 的 email
# - Token URL 返回格式需设置 Accept: application/json
# - 适合开发者社区，InstantBoard 技术用户可能偏好
```

#### Apple Sign-In

```python
# app/core/sso_handlers.py — AppleSSOHandler (:276)
AUTH_URL = "https://appleid.apple.com/auth/authorize"
TOKEN_URL = "https://appleid.apple.com/auth/token"
# 用户信息从 id_token JWT 解析

# 特殊要点:
# - 必须使用 Apple Developer 账号创建 Service ID
# - 需要 ES256 JWT (Apple 私钥签名 client_secret)
# - id_token 包含: sub (Apple用户ID), email, email_verified
# - 用户可能隐藏真实 email (Apple 提供代理 email)
# - 需要处理 "transfer" 事件 (用户注销 Apple ID 后 email 变化)
# - Refresh token 有效期长，但用户可随时撤销
# - 实现 JS SDK: https://developer.apple.com/sign-in-with-apple/get-started/
```

#### Facebook Login

```python
# app/core/sso_handlers.py — FacebookSSOHandler (:358)
AUTH_URL = "https://www.facebook.com/v18.0/dialog/oauth"
TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
USERINFO_URL = "https://graph.facebook.com/me?fields=id,name,email,picture"
SCOPE = "email public_profile"

# 特殊要点:
# - 必须在 Facebook App Dashboard 创建应用
# - email 可能不存在 (用户未授权/未验证)
# - 需要验证 access_token: app_id 匹配检查
# - Graph API 版本需要定期升级 (v18.0 → ...)
# - 用户头像通过 picture.width(N).height(N) 参数获取
# - FB 审核: publish_permissions 需要审核，仅用 login 不需要
```

#### 统一 SSO 内部架构

```python
# app/core/sso_handlers.py — 工厂类 SSOHandlerFactory (:422)
class SSOHandlerFactory:
    """统一入口，根据 provider 创建对应 handler（支持配置驱动启用/禁用）"""
    
    all_handlers = {
        "google": GoogleSSOHandler,
        "azure_ad": AzureADSSOHandler,
        "github": GitHubSSOHandler,
        "apple": AppleSSOHandler,
        "facebook": FacebookSSOHandler,
    }
    
    def get_enabled_providers() -> list[str]:
        """从 ENABLED_SSO_PROVIDERS 环境变量读取已启用列表"""
        return settings.ENABLED_SSO_PROVIDERS  # 默认 ["google", "github"]
    
    def create(provider: str) -> BaseSSOHandler:
        """创建 handler，若 provider 未启用则抛出 ValueError"""
        enabled = settings.ENABLED_SSO_PROVIDERS
        if provider not in enabled:
            raise ValueError(f"Provider '{provider}' is not enabled. Enabled: {enabled}")
        return all_handlers[provider](settings)

class BaseSSOHandler:
    """所有 SSO handler 的抽象基类"""
    
    abstract methods:
    - get_authorize_url(state: str) → str
    - exchange_code(code: str) → SSOTokenResponse
    - get_user_info(access_token: str) → SSOUserInfo
    
    common method:
    - authenticate(code: str) → User  # 创建/查找用户 + 生成JWT

class SSOUserInfo:
    provider: str
    provider_id: str       # SSO提供商的用户唯一ID
    email: str | None
    name: str | None
    avatar_url: str | None
    email_verified: bool | None  # None=提供商未返回；仅 Google 登录时强制校验（见 §3.4 Google 要点）
    raw_data: dict         # 提供商返回的原始数据
```

### 3.5 多租户隔离安全策略

| 层级 | 策略 | 实现 |
|------|------|------|
| 网络 | SSE/API 隔离 | 每个请求携带 tenant_id（JWT claims），依赖注入提取 |
| 数据库 | PostgreSQL RLS | ✅ **已实现**（迁移 `7d9a46a0d5c9`）：8 张含 `tenant_id` 的业务表（categories、sources、items、finance_symbols、finance_quotes、fund_nav_estimates、watchlist_items、sse_connections）`ENABLE + FORCE ROW LEVEL SECURITY` + `tenant_isolation` 策略：请求会话只读/写本租户行（+系统租户只读），后台会话经 `app.is_service=on` 旁路；`users`（登录先于租户上下文）与 `dashboard_snapshots`（全局运维数据）经评估排除。作为纵深防御兜底，应用层显式过滤仍是第一道防线（详见 database.md §3.5） |
| Redis | Key 前缀隔离 | 仅租户级数据键带 `t:{tenant_id}:` 前缀（见 database.md §3.2） |
| MongoDB | 查询条件隔离 | 所有查询 `{tenant_id: xxx}`（MongoDB 未启用，规划） |
| API | 依赖注入 | ⚠️ `TenantMiddleware` 不存在；实际为每端点 `Depends(get_current_tenant)`（dependencies.py:57-63）从 JWT 提取 tenant_id，service 层显式传参过滤 |
| 管理 | 租户配置限制 | max_users, max_categories, max_sources 约束 |
| 越权检测 | 每次查询验证 | 各查询人工携带 `tenant_id ==` 条件（无自动注入），依赖代码评审与测试；RLS 作为数据库层兜底——即便某条查询漏带租户条件，跨租户行仍会被 `tenant_isolation` 策略拦截（见数据库行） |

**租户管理员权限**:
- `admin` 角色: 可管理租户内用户、分类、数据源
- `member` 角色: 可使用功能、管理个人自选列表
- `viewer` 角色: 仅查看，不可修改

### 3.6 JWT/Session 管理方案

**双 Token 方案 (Access + Refresh)**:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph LR
  subgraph accessToken["Access Token"]
    ATexp["有效期: 1h 可配置"]
    ATstore["存储: 前端 localStorage"]
    ATuse["使用: Bearer Authorization header / SSE query param"]
    ATcontent["内容: user_id, tenant_id, role, exp"]
    ATsign["签名: HS256 JWT_SECRET"]
  end

  subgraph refreshToken["Refresh Token"]
    RTexp["有效期: 7d 可配置"]
    RTstore["存储: 前端 localStorage (refresh_token 键)"]
    RTuse["使用: POST /api/v1/auth/refresh<br/>JSON body 提交 (RefreshTokenRequest)"]
    RTcontent["内容: user_id, tenant_id, role, provider, refresh_version"]
    RTsign["签名: HS256 JWT_SECRET"]
    RTrotation["单次使用: 使用后旧token失效 返回新pair Rotation"]
    RTdetect["失效检测: Redis黑名单<br/>token_blacklist:{jti}<br/>TTL=剩余有效期"]
  end

  subgraph lifecycle["Token 生命周期"]
    S1["1. 登录 → 返回 access + refresh pair"]
    S2["2. Access过期 → 前端自动用refresh获取新pair"]
    S3["3. Refresh过期/失效 → 前端跳转登录页"]
    S4["4. 登出 → 后端将refresh token加入Redis黑名单 + 前端清除"]
  end

  S1 --> accessToken
  S1 --> refreshToken
  S2 --> refreshToken
  S2 --> accessToken
```

**存储与风险现状（如实描述）**：

- 两个 token 均存于前端 `localStorage`（键 `access_token` / `refresh_token`，见 `frontend/src/stores/auth.ts`）；后端**不设置任何 Cookie**（全代码库无 `set_cookie`/`Set-Cookie`），认证为纯 Bearer 方案。
- Access Token 通过 `Authorization: Bearer <token>` 请求头传递；Refresh Token 通过 `POST /api/v1/auth/refresh` 的 JSON body 提交；登出时 `DELETE /api/v1/auth/logout` 同样以 JSON body 携带 refresh token 供后端拉黑。
- SSE 连接复用 Access Token 作为 `token` query 参数（`frontend/src/utils/sse.ts` 每次（重）连接时重新读取 localStorage 中的最新 token）。

**主要风险：XSS 下 token 可被窃取**。`localStorage` 对同源脚本完全可读，一旦页面存在 XSS 注入，攻击者可直接读取并外传两个 token，冒充用户直至其自然过期。这是"不用 Cookie 存储"方案权衡后接受的代价，现有缓解措施：

| 缓解 | 说明 |
|------|------|
| Access Token 短时效 | 默认 60 分钟（`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`）；SSE 凭证也是 access token，泄露后的暴露窗口受同一时效限制 |
| Refresh 黑名单 + 单次轮换 | `POST /api/v1/auth/refresh` 使用后立即将旧 refresh token 加入 Redis 黑名单并签发新 token 对（`services/auth.py::refresh_token`）；被盗 refresh token 被任一方使用后即失效 |
| 登出全量失效 | 登出时 access + refresh 均进黑名单（access TTL = 剩余有效期；refresh TTL = 配置有效期，默认 7d），前端清除 localStorage |
| Admin 入口隔离 | `/ibadmin` 是独立会话入口（`session_entry=admin`），本地管理员与 SSO 用户按登录入口隔离、永不按 email 合并（见 [admin-login.md](admin-login.md) §2），SSO 侧被窃取的 token 无法接管管理员身份 |
| /ibadmin 防爆破锁定 | 管理员登录有 email + IP 双维度失败锁定（5 次/15 分钟、20 次/1 小时）、dummy bcrypt 时序拉平与统一错误文案（见 [admin-login.md](admin-login.md) §7） |
| XSS 预防基线 | Vue 默认转义（全前端无 v-html）+ nginx 基础安全头 + CSP（✅ 已实现，见 §3.2）；DOMPurify 当前仍无需（无 v-html） |

> 残余风险：refresh token（7d）若被窃取，攻击者可在有效期内静默轮换续期；已泄露的 access token 在自然过期前无法即时作废（仅登出/黑名单可提前终止）。如需更强保护，可评估将 refresh token 改回 HttpOnly Cookie 存储（需另行设计，会重新引入 CSRF 考量），当前版本未实现。

### 3.7 HTTPS 配置

```nginx
# nginx.prod.conf — SSL 配置
server {
    listen 443 ssl http2;
    
    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    
    # Mozilla Modern 配置
    ssl_protocols TLSv1.3;
    ssl_prefer_server_ciphers off;
    
    # HSTS — max-age 由环境变量 HSTS_MAX_AGE 配置 (默认 31536000=1年);
    # 刻意不带 preload (https-server.conf.template:13-20 注释说明:
    # preload 列表退出流程繁琐, 私有化部署不宜默认启用)
    add_header Strict-Transport-Security "max-age=${HSTS_MAX_AGE}; includeSubDomains" always;
    
    # SSL session cache
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
}

server {
    listen 80;
    # 实际: 重定向到配置的外部访问地址 (http-redirect.conf.template:31-33),
    # 使用 PUBLIC_BASE_URL 而非 $host, 避免 Host 头注入
    return 301 ${PUBLIC_BASE_URL}$request_uri;
}
```

**证书管理**: Let's Encrypt + certbot 自动续期 (生产服务器 cron job)

### 3.8 CORS 策略

```python
# FastAPI CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # .env 配置，生产仅允许实际域名
    allow_credentials=True,                # 现状保留；认证为纯 Bearer，无 Cookie 凭据，此项实际不涉及 Cookie
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
    max_age=3600,                          # CORS preflight 缓存1h
)

# SSE 特殊处理:
# EventSource 不发送 CORS preflight (简单GET)
# 但需要 Access-Control-Allow-Origin 响应头
# 实际现状: SSE 响应直接硬编码 Access-Control-Allow-Origin: * (api/v1/sse.py:129),
# 绕过本中间件的白名单 —— 待与 CORS 策略统一治理
```

### 3.9 已实现但此前未记录的安全机制（2026-08-24 审计补充）

| 机制 | 实现位置 | 说明 |
|------|---------|------|
| **JWT_SECRET 启动期强校验** | `config.py:189-217` | `ENV=staging/production` 时，占位符或长度 <32 的 secret 直接拒绝启动（RuntimeError）；非生产环境仅 WARNING |
| **管理员登录防爆破** | `services/auth.py:384-434` | email 5 次/15 分钟 + IP 20 次/1 小时双维度锁定、dummy bcrypt 时序拉平、统一错误文案——详见 [admin-login.md](admin-login.md) §7 |
| **Refresh kill-switch** | `services/auth.py:210-217` | 本地管理员登录被禁用后，拒绝 `provider=='local'` 会话的 refresh，实现"删除环境变量一键撤销" |
| **SSO 账户接管加固** | `services/auth.py:316-348` | email 回退匹配限定 default 租户且排除 `provider=='local'`——SSO 登录无法接管本地管理员记录，也不跨租户认领 |
| **system 租户禁删** | `api/v1/admin.py:218-219` | 管理 API 拒绝删除 system 租户，防止身份基础设施被破坏 |
| **X-Forwarded-For 防伪造** | `dependencies.py:101-127` | 客户端 IP 从右向左取第一个合法 IP；另 nginx 对 `/api/v1/auth/` location 用 `$remote_addr` 覆盖 XFF（见 admin-login.md §4.2） |
| ⚠️ 提醒：SSE 硬编码 `Access-Control-Allow-Origin: *` | `api/v1/sse.py:129` | 绕过 CORS 白名单；SSE 为只读数据且需 token，风险可控，待统一治理 |

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| CSRF防护 | 无Cookie认证(JWT Bearer) | SPA+Bearer天然免疫CSRF |
| 限流层级 | Nginx + FastAPI + 黑名单（✅ **L2 分级滑动窗口已实现**：Redis ZSET 60s 窗口、租户+IP+路由档位键、limit+burst 阈值、健康检查/SSE 豁免、fail-open，见 §3.3 层级 2；L1 未启用、L3 手工封禁已实现、自动封禁待攻击计数器逻辑） | 多层纵深防御 |
| SSO架构 | 统一OAuth2 + Provider适配器 | 5种SSO统一接口，新增provider只需加handler |
| JWT方案 | 双Token(Access+Refresh) | Access短效安全，Refresh长效方便 |
| 多租户 | 行级隔离+RLS ✅ 已实现（应用层显式过滤为主防线，8 表 `ENABLE+FORCE` RLS + `tenant_isolation` 策略为纵深兜底，后台经 `app.is_service` 旁路，见 §3.5 与 database.md §3.5） | 性能好、成本低 |
| CSP | 严格CSP策略（✅ **已实现**，nginx 模板硬编码下发，见 §3.2） | 防XSS，Vue默认转义减少风险 |

## 5. 边界情况

- **未启用的SSO提供商**: 返回 `VALIDATION_ERROR (400)`（provider 未启用或不受支持均用此码），前端通过 `GET /api/v1/auth/sso/providers` 预检查避免此情况
- **SSO提供商宕机**: 返回 `SSO_PROVIDER_ERROR (502)`，前端提示用户尝试其他SSO或稍后重试
- **JWT密钥泄露**: 管理API支持立即更换 `JWT_SECRET`，所有旧token自动失效
- **Redis限流不可用**: L2 中间件 **fail-open**（放行 + debug 日志，与栈内其他层口径一致）——可用性优先，限流在 Redis 恢复后自动重新生效；此时原规划的"降级 Nginx 层限流"**并不存在**（L1 未启用，见 §3.3 层级 1），Redis 故障期间的抗滥用依赖管理员手工封禁（L3，同样依赖 Redis 故同窗失效）与 auth 层防爆破锁定，属接受的风险窗口
- **Redis黑名单不可用**: 中间件读不到黑名单时 **fail-open**（放行 + debug 日志）——可用性优先，封禁在 Redis 恢复后自动重新生效；手工封禁因此**不是**抗 DDoS 的硬保证，恶性流量仍需 L1 兜底（L2 与 Redis 同生死，见上一条）
- **Redis不可用（OAuth state）**: authorize 存 state 与登录校验 state 均降级为 fail-open（warning 日志），SSO 登录保持可用；详见 §3.2 SSO OAuth State 行的决策说明
- **Apple代理email**: 用户隐藏真实email时，使用代理email，不可变更
- **多SSO同一email（身份隔离）**: 按登录入口隔离，**不做跨 email 合并**（见 [admin-login.md](admin-login.md) §2、`app/services/auth.py::_get_or_create_user`）：
  - 主路径：按 `(sso_provider, sso_provider_id)` 精确匹配，命中则刷新 profile 后返回；
  - 邮箱回退：仅当**同一 email 已存在于 default 租户、且 `sso_provider != 'local'`** 时，才把该记录绑定到当前 provider（用于为同一人避免重复建档）；
  - **`local` 记录永不被 SSO 合并/接管**——即使 email 与本地管理员相同，二者也是不同租户下的独立记录（管理员在 system 租户）；
  - 不匹配时新建 default 租户的 `member` 用户；**不跨租户匹配**
- **CORS配置错误**: 生产环境仅允许实际域名，不使用 `*`

## 6. 与其他模块的依赖

- → [api.md](api.md): 认证端点定义
- → [database.md](database.md): RLS配置、用户表结构
- → [infrastructure.md](infrastructure.md): Nginx配置、SSL证书
- → [architecture.md](architecture.md): 整体安全架构层级