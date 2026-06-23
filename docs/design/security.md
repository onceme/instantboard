---
version: 1.0
author: designer
date: 2026-06-23
status: draft
cross_refs: [architecture.md, api.md, database.md, infrastructure.md]
---

# InstantBoard 安全设计

## 1. 目标

定义 InstantBoard 的完整安全方案，包括注入防护、DDoS防护、SSO集成、多租户隔离安全、JWT管理、HTTPS和CORS，确保系统安全可靠。

## 2. 方案概述

采用 **多层防御 (Defense in Depth)** 策略：Nginx层限流/SSL → FastAPI中间件层认证/隔离 → 数据层RLS/参数化查询，5种SSO通过统一OAuth2流程集成。

## 3. 详细设计

### 3.1 SQL 注入防护方案

| 层级 | 方案 | 实现 |
|------|------|------|
| ORM层 | **全部使用 SQLAlchemy ORM** | 不手写SQL，所有查询通过 ORM 生成参数化查询 |
| 原始SQL | **严格禁止** | 如需原始SQL（仅迁移脚本），使用 `text()` + 参数绑定 |
| 输入验证 | **Pydantic schema** | 所有API输入经过Pydantic验证，类型+长度+格式约束 |
| 特殊字符 | **输入清洗** | 用户输入的搜索关键词，转义 `%` 和 `_` (LIKE操作) |

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
| CSP Header | Nginx 配置 `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https:; connect-src 'self'` |
| DOMPurify | 唯一使用 v-html 的场景 (新闻摘要)，前端用 DOMPurify 清洗 |
| Cookie | `HttpOnly; Secure; SameSite=Strict` — JWT 不入 Cookie，纯Bearer |

#### CSRF 阻护

| 方案 | 实现 |
|------|------|
| 不使用Cookie认证 | JWT Bearer Token → CSRF 自然免疫 (无自动发送的Cookie) |
| SameSite Cookie | Refresh Token Cookie 设置 `SameSite=Strict` |
| SSO OAuth State | `state` 参数防 CSRF — state 存 Redis (`sso_state:{key}` TTL 10min) |
| SSE Token | SSE 认证用 query param `token` — 不受 CSRF 影响 |
| Origin验证 | FastAPI 中间件验证 `Origin` / `Referer` 头匹配 CORS 白名单 |

### 3.3 DDoS 防护方案

#### 速率限制 (多层级)

**层级 1: Nginx 限流 (粗粒度)**

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

```python
# auth/rate_limit.py
class RateLimitMiddleware:
    # 基于 Redis 的滑动窗口限流
    # Key: rate:{tenant_id}:{ip}:{endpoint_prefix}
    # 每个租户有独立的限流配置
    
    Default limits:
    - 全局 API: 60 requests/min per IP per tenant
    - 认证: 5 requests/min per IP
    - SSE: 3 connections per user
    - 搜索: 20 requests/min per user
```

**层级 3: IP 黑名单**

```python
# auth/rate_limit.py
IP_BLACKLIST_REDIS_KEY = "ip_blacklist"
# 自动封禁: 连续触发限流5次 → 自动加入黑名单 TTL 1h
# 管理员手动封禁: Dashboard API 添加永久黑名单
# 检查: FastAPI 中间件最先执行黑名单检查
```

**层级 4: 请求验证**

- 验证 `User-Agent` 存在且非空 (阻止简单脚本)
- 验证请求大小 < 10KB (阻止超大请求)
- API 端点验证 JWT Token 格式 (不解析，仅格式检查)
- SSE 端点验证 `token` query param 存在

**DDoS 防护层级总结**:

| 层级 | 技术 | 保护对象 | 限制粒度 |
|------|------|---------|---------|
| L1: Nginx | limit_req | 全局 | IP级 |
| L2: FastAPI | Redis滑动窗口 | 租户级 | IP+租户级 |
| L3: 黑名单 | Redis Set + 中间件 | 恶意IP | IP级 |
| L4: 请求验证 | Header检查 | 异常请求 | 请求级 |

### 3.4 SSO 集成设计 (5种最主流)

#### 统一OAuth2 流程架构

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant BE as Backend
    participant SSO as SSO Provider

    FE->>BE: 1. GET /api/v1/auth/sso/{provider}/authorize
    BE-->>FE: 返回授权URL
    FE->>SSO: 2. 重定向到SSO提供商授权页面
    SSO-->>FE: 3. 用户授权 → 回调到InstantBoard callback URL
    FE->>BE: 4. POST /api/v1/auth/sso/{provider} 发送code
    BE->>SSO: 5a. code → access_token 提供商
    SSO-->>BE: 5b. 返回access_token
    BE->>BE: 5c. access_token → 用户信息 → 创建/查找user → 生成JWT
    BE-->>FE: 6. 返回 JWT access_token + refresh_token

    Note over BE: 内部实现差异仅在Step 5的<br/>"code → access_token → 用户信息"部分
```

#### Google OAuth2

```python
# auth/sso.py — GoogleOAuthHandler
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"

# 特殊要点:
# - 必须在 Google Cloud Console 创建 OAuth 2.0 Client
# - 支持 HD 参数限制组织域名 (企业租户)
# - Token 包含 email_verified 字段，必须验证
# - Refresh token 仅在首次授权时返回
```

#### Microsoft Azure AD

```python
# auth/sso.py — AzureADHandler
AUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
USERINFO_URL = "https://graph.microsoft.com/oidc/userinfo"  # 或直接从 id_token 解析
SCOPE = "openid email profile User.Read"

# 特殊要点:
# - tenant_id 区分: common (多租户) / organizations (仅组织) / consumers (仅个人)
# - id_token 是 JWT，可直接解析用户信息 (减少一次网络请求)
# - 支持 conditional access policies (条件访问策略)
# - 组织管理员可限制仅公司域名登录
```

#### GitHub OAuth

```python
# auth/sso.py — GitHubOAuthHandler
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
# auth/sso.py — AppleSignInHandler
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
# auth/sso.py — FacebookLoginHandler
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
# auth/sso.py
class SSOHandlerFactory:
    """统一入口，根据 provider 创建对应 handler"""
    
    handlers = {
        "google": GoogleOAuthHandler,
        "azure_ad": AzureADHandler,
        "github": GitHubOAuthHandler,
        "apple": AppleSignInHandler,
        "facebook": FacebookLoginHandler,
    }
    
    def create(provider: str) -> BaseSSOHandler:
        return handlers[provider](settings)

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
    email_verified: bool
    raw_data: dict         # 提供商返回的原始数据
```

### 3.5 多租户隔离安全策略

| 层级 | 策略 | 实现 |
|------|------|------|
| 网络 | SSE/API 隔离 | 每个请求携带 tenant_id，中间件注入 |
| 数据库 | PostgreSQL RLS | `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + policy |
| Redis | Key 前缀隔离 | `t:{tenant_id}:xxx` |
| MongoDB | 查询条件隔离 | 所有查询 `{tenant_id: xxx}` |
| API | 中间件注入 | `TenantMiddleware` 从 JWT 提取 tenant_id |
| 管理 | 租户配置限制 | max_users, max_categories, max_sources 约束 |
| 越权检测 | 每次查询验证 | SQLAlchemy session 过滤 + RLS 双重保障 |

**租户管理员权限**:
- `admin` 角色: 可管理租户内用户、分类、数据源
- `member` 角色: 可使用功能、管理个人自选列表
- `viewer` 角色: 仅查看，不可修改

### 3.6 JWT/Session 管理方案

**双 Token 方案 (Access + Refresh)**:

```mermaid
graph LR
  subgraph accessToken["Access Token"]
    ATexp["有效期: 1h 可配置"]
    ATstore["存储: 前端 localStorage"]
    ATuse["使用: Bearer Authorization header / SSE query param"]
    ATcontent["内容: user_id, tenant_id, role, exp"]
    ATsign["签名: HS256 SECRET_KEY"]
  end

  subgraph refreshToken["Refresh Token"]
    RTexp["有效期: 7d 可配置"]
    RTstore["存储: 前端 Cookie HttpOnly, Secure, SameSite=Strict"]
    RTuse["使用: POST /api/v1/auth/refresh"]
    RTcontent["内容: user_id, tenant_id, refresh_version"]
    RTsign["签名: HS256 JWT_SECRET"]
    RTrotation["单次使用: 使用后旧token失效 返回新pair Rotation"]
    RTdetect["失效检测: Redis存储used refresh tokens TTL=7d"]
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
    
    # HSTS
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    
    # SSL session cache
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
}

server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

**证书管理**: Let's Encrypt + certbot 自动续期 (生产服务器 cron job)

### 3.8 CORS 策略

```python
# FastAPI CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # .env 配置，生产仅允许实际域名
    allow_credentials=True,                # Refresh token Cookie 需要
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
    max_age=3600,                          # CORS preflight 缓存1h
)

# SSE 特殊处理:
# EventSource 不发送 CORS preflight (简单GET)
# 但需要 Access-Control-Allow-Origin 响应头
# Nginx 对 /api/v1/stream/ 添加: add_header Access-Control-Allow-Origin $cors_origin;
```

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| CSRF防护 | 无Cookie认证(JWT Bearer) | SPA+Bearer天然免疫CSRF |
| 限流层级 | Nginx + FastAPI + 黑名单 | 多层纵深防御 |
| SSO架构 | 统一OAuth2 + Provider适配器 | 5种SSO统一接口，新增provider只需加handler |
| JWT方案 | 双Token(Access+Refresh) | Access短效安全，Refresh长效方便 |
| 多租户 | 行级隔离+RLS | 性能好、成本低、RLS安全保障 |
| CSP | 严格CSP策略 | 防XSS，Vue默认转义减少风险 |

## 5. 边界情况

- **SSO提供商宕机**: 返回 `SSO_PROVIDER_ERROR (401)`，前端提示用户尝试其他SSO或稍后重试
- **JWT密钥泄露**: 管理API支持立即更换 `SECRET_KEY`，所有旧token自动失效
- **Redis限流不可用**: 降级为 Nginx 层限流 (粗粒度但有效)
- **Apple代理email**: 用户隐藏真实email时，使用代理email，不可变更
- **多SSO同一email**: 同一email不同provider → 创建同一tenant下的同一user (合并逻辑)
- **CORS配置错误**: 生产环境仅允许实际域名，不使用 `*`

## 6. 与其他模块的依赖

- → [api.md](api.md): 认证端点定义
- → [database.md](database.md): RLS配置、用户表结构
- → [infrastructure.md](infrastructure.md): Nginx配置、SSL证书
- → [architecture.md](architecture.md): 整体安全架构层级