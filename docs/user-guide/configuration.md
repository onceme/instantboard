# 配置说明

## 环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

**关键配置分组**：

### 基础配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENV` | `development` | 运行环境 (development/production) |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `API_PORT` | `8000` | 后端 API 端口 |
| `FRONTEND_PORT` | `3000` | 前端开发端口 |
| `SCHEDULER_ENABLED` | `true` | 是否启动内嵌调度器（生产 api 容器由 compose 强制置 `false`，采集由独立 worker 容器承担） |
| `SSL_CERT_FILE` | `/etc/ssl/certs/ca-certificates.crt` | CA 证书路径（后端镜像已内置，供外部 HTTPS 采集使用） |

### 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL 连接串 |
| `DB_PASSWORD` | `devpass` | 数据库密码 |
| `DATABASE_POOL_SIZE` | `20` | 连接池大小 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |

### 认证与 JWT

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JWT_SECRET` | *(需修改)* | JWT 签名密钥（access + refresh token，生产必须替换！） |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access Token 有效期 |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh Token 有效期 |

### SSO OAuth（按需配置）

InstantBoard 支持 5 种 SSO 提供商，**默认只启用 Google 和 GitHub**。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLED_SSO_PROVIDERS` | `google,github` | 启用的 SSO 提供商（逗号分隔） |

**启用提供商配置示例**：

```bash
# 默认启用 Google 和 GitHub
ENABLED_SSO_PROVIDERS=google,github

# 额外启用 Azure AD
ENABLED_SSO_PROVIDERS=google,github,azure_ad

# 启用所有提供商
ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook
```

**各提供商凭据配置**：

| 变量组 | 说明 |
|--------|------|
| `GOOGLE_OAUTH_CLIENT_ID/SECRET` | Google OAuth 2.0 |
| `GITHUB_OAUTH_CLIENT_ID/SECRET` | GitHub OAuth *(默认启用)* |
| `AZURE_AD_CLIENT_ID/SECRET/TENANT_ID` | Microsoft Azure AD |
| `APPLE_CLIENT_ID/TEAM_ID/KEY_ID/Private_KEY_PATH` | Apple Sign-In |
| `FACEBOOK_APP_ID/SECRET` | Facebook Login |

> 💡 只有被 `ENABLED_SSO_PROVIDERS` 启用的提供商才会出现在前端登录页面，未配置的提供商会自动隐藏。

### 财经数据 API

| 变量 | 说明 |
|------|------|
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API Key (备用数据源) |
| `FINNHUB_API_KEY` | Finnhub API Key (可选) |
| `FINNHUB_API_KEYS` | Finnhub 多 Key 列表（逗号分隔，采集器轮换使用以规避限流） |

> yfinance (Yahoo Finance) 为主数据源，无需 API Key。

### SSE & 限流

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SSE_HEARTBEAT_INTERVAL` | `30` | SSE 心跳间隔（秒） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每分钟请求限制 |
| `RATE_LIMIT_BURST` | `10` | 突发请求量 |

---

生产环境的完整 `.env` 模板、SSL/HTTPS 与部署专属变量见[生产部署](deployment.md)。
