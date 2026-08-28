# 生产部署

> 最后修订：2026-08-24（文档与代码对照审计后修订）

本页是部署信息的唯一归属：部署流程、SSL/HTTPS、多架构与生产环境 `.env` 模板。环境变量字段含义见[配置说明](configuration.md)；完整的 Docker 编排方案见 [基础设施设计](../dev-guide/design/infrastructure.md)。

## 部署步骤（Docker Compose）

```bash
# 1. 准备生产环境配置
cp .env.example .env
# 编辑 .env，填写所有 PROD_* 变量

# 2. 准备 SSL 证书
mkdir -p docker/nginx/ssl
# 放置 fullchain.pem 和 privkey.pem
# 推荐使用 Let's Encrypt + certbot

# 3. 构建并启动
make build-prod
make prod-up
```

开发环境启动（`make dev`）见[快速开始](getting-started.md)。

Makefile 和所有 shell 脚本均自动检测 `docker compose`（V2 插件）与 `docker-compose`（V1 独立版），两者均可使用，无需手动配置。

## 生产环境服务清单

| 服务 | 说明 | 资源限制 |
|------|------|---------|
| `nginx` | 反向代理 + SSL | 0.5 CPU / 256M |
| `api` | FastAPI + Gunicorn（`SCHEDULER_ENABLED=false`，调度器由 worker 独占） | 1.0 CPU / 512M |
| `worker` | 后台采集任务（内嵌 APScheduler + 心跳） | 0.5 CPU / 256M |
| `postgres` | PostgreSQL **15**-alpine（数据目录由 15 初始化，升级 17 需先 pg_dump/restore 停机迁移） | 1.0 CPU / 768M |
| `redis` | Redis 7 (密码保护) | 0.5 CPU / 256M |
| `frontend` | 静态资源 (build 产物) | 0.25 CPU / 128M |
| `mongodb` | 可选，MongoDB 6（`--profile mongodb` 按需启用，默认不启动） | 0.5 CPU / 512M |

## 开发 vs 生产差异

| 项目 | 开发 | 生产 |
|------|------|------|
| API | 端口 8000 直接访问 | Nginx 代理 (80/443) |
| 数据库 | 端口 5432 可直连 | 不对外暴露 |
| 前端 | Vite dev server (HMR) | Nginx 托管构建产物 |
| Worker | 集成在 API (APScheduler) | 独立 Worker 进程 |
| HTTPS | 无 | TLS 1.3 + HSTS |
| 重启策略 | 无 | always |
| 密码 | 固定开发密码 | .env 强密码 |

## SSO 提供商配置

InstantBoard 支持 5 种 SSO 提供商（Google、GitHub、Azure AD、Apple、Facebook），**默认只启用 Google 和 GitHub**。

### 环境变量配置

在 `.env` 文件中配置 `ENABLED_SSO_PROVIDERS`：

```bash
# ============================================
# 默认配置 — 仅启用 Google 和 GitHub
# ============================================
ENABLED_SSO_PROVIDERS=google,github
GOOGLE_OAUTH_CLIENT_ID=your_google_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_google_client_secret
GITHUB_OAUTH_CLIENT_ID=your_github_client_id
GITHUB_OAUTH_CLIENT_SECRET=your_github_client_secret
```

### 启用 Azure AD

```bash
# ============================================
# 启用 Azure AD（额外添加）
# ============================================
ENABLED_SSO_PROVIDERS=google,github,azure_ad

# Google + GitHub 凭据（保持不变）
GOOGLE_OAUTH_CLIENT_ID=your_google_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_google_client_secret
GITHUB_OAUTH_CLIENT_ID=your_github_client_id
GITHUB_OAUTH_CLIENT_SECRET=your_github_client_secret

# Azure AD 凭据（新增）
AZURE_AD_CLIENT_ID=your_azure_ad_client_id
AZURE_AD_CLIENT_SECRET=your_azure_ad_client_secret
AZURE_AD_TENANT_ID=your_tenant_id
```

### 启用 Apple Sign In

```bash
ENABLED_SSO_PROVIDERS=google,github,apple

# Apple 凭据
APPLE_CLIENT_ID=your_apple_service_id
APPLE_TEAM_ID=your_team_id
APPLE_KEY_ID=your_key_id
APPLE_PRIVATE_KEY_PATH=/path/to/AuthKey.p8
```

### 启用 Facebook Login

```bash
ENABLED_SSO_PROVIDERS=google,github,facebook

# Facebook 凭据
FACEBOOK_APP_ID=your_facebook_app_id
FACEBOOK_APP_SECRET=your_facebook_app_secret
```

### 启用所有提供商

```bash
ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook
# 然后配置所有提供商的凭据...
```

### 查询已启用的提供商

前端可通过 API 动态获取当前启用的 SSO 提供商列表：

```bash
curl http://localhost:8000/api/v1/auth/sso/providers
# 响应:
# {"success": true, "data": {"enabled_providers": ["google", "github"]}}
```

> 💡 该端点不需要认证，前端可用此接口在登录页面动态显示对应的登录按钮。

### 设计说明

- **配置驱动**：所有 5 种提供商的代码实现均已保留，启用/禁用仅通过环境变量控制
- **向后兼容**：数据库 CHECK 约束包含全部 5 个提供商值，不受配置影响
- **安全校验**：未在 `ENABLED_SSO_PROVIDERS` 中的提供商调用登录接口时，返回 `400`、错误码 **`VALIDATION_ERROR`**（`core/sso_handlers.py:436` 抛出 `ValueError`，由 `services/auth.py:65-67` 包装为 `ValidationError`）。代码中不存在 `PROVIDER_NOT_ENABLED` 错误码
- **前端集成**：前端通过 `GET /api/v1/auth/sso/providers` 接口获取启用列表，未配置的提供商登录按钮自动隐藏

## JWT 配置

```bash
JWT_SECRET=your-jwt-secret-at-least-32-chars
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

## 完整生产环境 .env 示例

> 各变量的含义见[配置说明](configuration.md)，此处只给出生产模板。

```bash
# --- 通用 ---
ENV=production
LOG_LEVEL=WARNING

# --- 数据库 ---
DATABASE_URL=postgresql+asyncpg://instantboard:secure_password@postgres:5432/instantboard
REDIS_URL=redis://:secure_redis_password@redis:6379/0

# --- SSO（默认只启用 Google + GitHub）---
ENABLED_SSO_PROVIDERS=google,github
GOOGLE_OAUTH_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your_secret
GITHUB_OAUTH_CLIENT_ID=your_client_id
GITHUB_OAUTH_CLIENT_SECRET=your_secret

# --- JWT ---
JWT_SECRET=<strong-random-key>

# --- CORS ---
CORS_ORIGINS=https://your-domain.com

# --- Financial Data ---
ALPHA_VANTAGE_API_KEY=your_key
```

### 其他部署相关环境变量

| 变量 | 说明 |
|------|------|
| `ADMIN_PASSWORD` | **仅 `ENV` 非生产时可用**的明文便捷项：启动时被一次性 bcrypt 哈希后丢弃明文；`ENV=production` 时被忽略并记 error 日志，生产必须用 `ADMIN_PASSWORD_HASH`（`.env.example:79-83`） |
| `SSL_CERT_FILE` | Python 出站 HTTPS 校验使用的 CA bundle（`.env.example:152-161`）；Docker 镜像已在 Dockerfile 内置该环境，仅本地 venv 等非 Docker 运行需要设置 |
| `FINNHUB_API_KEYS` | Finnhub 多 Key 列表（轮换配额用）；另有单 Key 版 `FINNHUB_API_KEY` |

## GitHub Secrets 必要清单

部署 workflow 依赖以下 GitHub Repository Secrets（Settings → Secrets → Actions）：

- `STAGING_HOST` / `STAGING_PORT` / `STAGING_USER` / `STAGING_SSH_KEY` — staging 服务器 SSH 凭证
- `STAGING_DOMAIN=ib.bithollow.org` — smoke test 的回退域名（`STAGING_SITE_ORIGIN` 未配置时使用）
- `STAGING_SITE_ORIGIN` — staging 站点的完整公网 origin（含 scheme 与公网端口），如
  `https://ib.bithollow.org:65533`。配置后冒烟测试直接探测该 origin；**未配置时回退**到
  `http://${STAGING_DOMAIN}:${STAGING_SITE_PORT:-65533}`，与切换前的纯 HTTP 行为保持兼容
- `STAGING_SITE_PORT` — 公网站点端口（默认 65533），仅用于上述回退逻辑
- `PROD_HOST` / `PROD_USER` / `PROD_SSH_KEY` — 生产服务器 SSH 凭证
- `PROD_PORT` — 生产服务器 SSH 端口（可选，默认 22；未配置/留空时 `port: ` 为空，
  `appleboy/ssh-action@v1.0.3` 会回退到标准 22 端口而非报错；仅当将其配置为非数字值时
  部署动作才会解析失败）
- `PROD_DOMAIN` — 用于部署后 health check
- `PROD_DATABASE_URL` / `PROD_REDIS_URL` / `PROD_SECRET_KEY` / `PROD_JWT_SECRET` / `PROD_CORS_ORIGINS` — 生产环境敏感配置
- `PROD_DB_PASSWORD` / `PROD_REDIS_PASSWORD` — 生产 PostgreSQL / Redis 密码。`docker-compose.prod.yml` 使用 `${...:?...}` 强制要求（:177、:201、:203），**缺失则容器启动直接失败**

> **Smoke test 说明**：`cd-staging.yml` 的冒烟测试依次探测
> `${BASE_URL}/api/v1/health` 与 `${BASE_URL}/`（最多 6 次、间隔 5 秒），任一失败即
> `exit 1` 阻断部署（历史上曾有 `|| echo` 兜底吞掉失败，已移除）。`BASE_URL` 由
> `STAGING_SITE_ORIGIN` secret 驱动：staging 切到 HTTPS 后，只需把该 secret 配成
> `https://ib.bithollow.org:65533`，无需改任何代码。

### ⚠️ `make prod-up` 的 .env 可见性问题（重要）

Makefile 的 `COMPOSE_PROD` 为 `cd docker && docker compose -f docker-compose.yml -f docker-compose.prod.yml`（Makefile:19），**在 `docker/` 目录执行且不传 `--env-file`**。Compose 的 `${PROD_*}` 插值只读取当前工作目录下的 `.env`，即 `docker/.env`，**不会读取仓库根目录的 `.env`**。因此：

- 直接 `make prod-up` 时，若 `PROD_*` 只写在仓库根 `.env`，会因 `:?` 校验失败而无法启动；
- CI 已用显式 `--env-file .env` 规避（`cd-staging.yml:110`，`cd-production.yml` 各部署步骤同理）；
- 本地手动部署二选一：把 `PROD_*` 写入 `docker/.env`，或命令显式附加 `--env-file ../.env`。

## 启用 HTTPS（staging 切换流程）

公网拓扑：`https://ib.bithollow.org:65533`（公网 65533）→ 用户防火墙 →
staging 宿主机 `10443`（即 `.env` 的 `HTTPS_PORT`）→ nginx 容器 HTTPS 端口。
**公网端口与宿主机/容器端口不一致**，因此一切对外可见的 URL（重定向目标、
OAuth 回调、CORS origin）都必须使用公网口径（65533），仓库内不得出现容器端口的
硬编码。过渡期防火墙映射为 `65533→10080`（HTTP），切换后改为 `65533→10443`。

前端/后端已按"同源 + 运行时推导"设计，无需改代码：

- 前端 API baseURL（`frontend/src/utils/api.ts`）与 SSE 地址（`frontend/src/utils/sse.ts`）
  均为相对路径 `/api/v1/...`，跟随页面 scheme 自动变 https；
- SSO `redirect_uri` 由 `frontend/src/composables/useAuth.ts` 用
  `window.location.origin` 运行时生成（自动变为
  `https://ib.bithollow.org:65533/auth/callback`），后端全程透传
  （`app/api/v1/auth.py` / `app/core/sso_handlers.py`），无 scheme/域名硬编码。

### 切换步骤

#### 1. 证书就位检查

`SSL_CERT_DIR` 指向的宿主机目录中必须有 `fullchain.pem` + `privkey.pem`
（`docker-compose.prod.yml` 将其只读挂载到容器 `/etc/nginx/ssl`；文件缺失时
nginx 启动直接失败）。确认证书有效域名与有效期：

```bash
openssl x509 -in "$SSL_CERT_DIR/fullchain.pem" -noout -subject -dates
```

证书域名需覆盖 `ib.bithollow.org`，且 `notAfter` 距当前有足够余量（续期机制见文末清单）。

#### 2. `.env` 三项改动

```bash
ENABLE_HTTPS=true
CORS_ORIGINS=https://ib.bithollow.org:65533      # 在原有值基础上追加/替换为 https 公网 origin
PUBLIC_BASE_URL=https://ib.bithollow.org:65533   # HTTP→HTTPS 301 的跳转目标，必须是公网口径
```

说明：

- `PUBLIC_BASE_URL` 用于渲染 `http-redirect.conf.template` 的 301 目标。**必须填公网
  端口**（65533）而非宿主机端口（10443），否则重定向指向公网不可达的地址。留空时
  `entrypoint.sh` 回退为 `https://$host`，仅适用于标准 443 端口场景。
- 可选：`HSTS_MAX_AGE`（Strict-Transport-Security 的 max-age 秒数，默认
  `31536000` = 1 年）。nginx 配置**永不启用 `preload`**——站点使用非标公网端口，
  而 preload 列表只按主机名生效，一旦加入会强制该域名所有端口永远走 HTTPS，
  污染该域名未来的其他用途；生产稳定运行后再评估是否加长 max-age。

#### 3. 防火墙映射切换

把防火墙上的公网映射从 `65533→10080`（HTTP）改为 `65533→10443`（HTTPS）。
建议在 `.env` 改完、容器重建（步骤 5）之后切换，减少服务空窗。

#### 4. OAuth 回调地址更新

- **GitHub OAuth App**（Settings → Developer settings → OAuth Apps → InstantBoard →
  Authorization callback URL）：改为 `https://ib.bithollow.org:65533/auth/callback`
- **Google Cloud OAuth 客户端**（若启用）：在"已获授权的重定向 URI"中加入
  `https://ib.bithollow.org:65533/auth/callback`
- 其他已启用的 SSO 提供商同理。回调地址必须与前端运行时生成的
  `window.location.origin + /auth/callback` 完全一致（含公网端口）。

#### 5. 配置 GitHub Secret 并重建容器

- 把 `STAGING_SITE_ORIGIN` secret 设为 `https://ib.bithollow.org:65533`
  （让 CD 冒烟测试探测 https，见上文"GitHub Secrets 必要清单"）。
- 在 staging 宿主机执行：

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  --env-file .env up -d --force-recreate nginx api worker
```

（前端静态资源容器与 nginx 共享卷，如非全栈重建也无需单独处理。）

#### 6. 验收项

- `https://ib.bithollow.org:65533/` 与 `/login` 正常打开（浏览器锁标志有效）；
- `curl -I http://ib.bithollow.org:65533/` 返回 `301`，`Location` 为
  `https://ib.bithollow.org:65533/...`（公网口径，**不是** `:10443`）；
- `curl -I https://ib.bithollow.org:65533/` 响应头包含
  `Strict-Transport-Security: max-age=...; includeSubDomains`（无 `preload`）；
- 安全头已启用：同一 `curl -I` 响应头还应包含 `Content-Security-Policy: default-src 'self'; ...`
  以及 `X-Frame-Options`、`X-Content-Type-Options`、`X-XSS-Protection`、`Referrer-Policy`
  （CSP 为 nginx 模板硬编码常量，策略与调优见 `docs/dev-guide/design/security.md` §3.2）；
- SSO 全流程：登录页 → 跳转提供商 → 回调 → 登录成功；
- 仪表盘 SSE 指示器变绿（EventSource 走 **https 同源**；SSE 是 HTTP 长连接，无 `wss` 协议）
- `docker compose ps` 中 nginx 持续 `healthy`（healthcheck 探测 `/healthz`，
  不受 301 影响）。

#### 7. 回滚步骤

反向操作即可，无状态迁移：

1. `.env`：`ENABLE_HTTPS=false`（可同时移除/注释 `PUBLIC_BASE_URL`；`CORS_ORIGINS` 恢复为
   纯 http origin）；
2. 防火墙映射改回 `65533→10080`；
3. OAuth 回调地址改回 `http://ib.bithollow.org:65533/auth/callback`；
4. `STAGING_SITE_ORIGIN` secret 删除（冒烟测试自动回退到 http 探测）；
5. `docker compose ... up -d --force-recreate nginx`。

> 注意：回滚后，曾经访问过 https 版本的用户浏览器在 `HSTS_MAX_AGE` 到期前会继续
> 强制走 HTTPS（HSTS 由浏览器本地缓存）。staging 默认 1 年的原因之一即在于此——
> 若对回滚灵活性要求高，可将 `HSTS_MAX_AGE` 调小（如 86400 = 1 天）。

### 已知风险（不阻塞）

- **access log 记录 SSE token**：`docker/nginx/nginx.conf` 的 `log_format main`
  包含 `$request`（完整请求行，含 query string），SSE 连接以
  `/api/v1/stream/<category>?token=<JWT>` 形式鉴权，该 token 会随请求行写入
  `access.log`（挂载卷内）。JWT 有效期有限（见 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`），
  但仍应避免将该日志外发/长期留存。后续可将 SSE 的鉴权改为 `Authorization` 头
  或在 nginx 侧脱敏后再修。

## SSL 证书与 Nginx 端口配置

Nginx 的 HTTPS 行为和监听端口完全由 `.env` 控制：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVER_NAME` | `_` | nginx server_name；本地用 `_`（任意 host），生产用真实域名 |
| `HTTP_PORT` | `80` | HTTP 监听端口（也用于外部映射） |
| `HTTPS_PORT` | `443` | HTTPS 监听端口（仅当 `ENABLE_HTTPS=true` 时监听）；注意这是宿主/容器侧端口，公网端口可能不同 |
| `ENABLE_HTTPS` | `false` | 设为 `true` 启用 HTTPS + HTTP→HTTPS 301 跳转（见上文"启用 HTTPS"切换流程） |
| `PUBLIC_BASE_URL` | `https://ib.bithollow.org:65533` | `.env.example:148` 内置值；对外可见的站点 origin（含公网端口），HTTP→HTTPS 301 跳转目标。**注意**：`https://$host` 不是默认值，而是该变量为空时 `entrypoint.sh:17-21` 的运行时回退（仅适用标准 443 场景） |
| `HSTS_MAX_AGE` | `31536000` | HSTS max-age（秒）；配置永不包含 `preload`（非标公网端口禁用） |
| `SSL_CERT_DIR` | `./nginx/ssl` | 证书目录路径（含 `fullchain.pem`/`privkey.pem`） |

**本地开发 .env**：保持默认即可，HTTP-only 模式。

**生产/预生产 .env** 示例：
```bash
SERVER_NAME=ib.bithollow.org
HTTP_PORT=8080          # 如果 80 被占用
HTTPS_PORT=443
ENABLE_HTTPS=true
SSL_CERT_DIR=/etc/letsencrypt/live/ib.bithollow.org
```

> 模板在 `docker/nginx/conf.d/*.template`，启动时 `entrypoint.sh` 用 envsubst 渲染到容器的 `/etc/nginx/conf.d/`。

> **注意**：`.gitignore` 应忽略服务器本地的 `.env` 文件，生产 `.env` 不应提交到仓库。

## 架构支持

项目 CI 同时构建 `linux/amd64` 和 `linux/arm/v7` 镜像，支持部署到：

- x86_64 服务器（amd64）
- ARM 32 位设备（如树莓派 3/4 装 32 位系统）

CI 使用 QEMU + buildx 构建多架构 manifest list 并推送至 GHCR，部署服务器 `docker pull` 时自动选择匹配架构。

> **注意**：`mongo:6` 镜像不提供 arm/v7 版本。若服务器为 arm/v7 架构，请勿启用 `--profile mongodb`。

## 多架构部署

### 支持的架构

| 架构 | 说明 |
|------|------|
| `linux/amd64` | x86_64 服务器、VM、工作站 |
| `linux/arm/v7` | ARM 32 位设备（如树莓派 3/4 装 32 位系统） |

### 手动部署时指定镜像标签

`docker-compose.prod.yml` 中镜像地址支持以下环境变量覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IMAGE_REGISTRY` | `ghcr.io` | 镜像仓库地址 |
| `IMAGE_PREFIX` | `onceme/instantboard` | 镜像名前缀 |
| `IMAGE_TAG` | `latest` | 镜像标签 |

手动部署示例：

```bash
IMAGE_TAG=v1.2.3 docker compose -f docker-compose.prod.yml pull
IMAGE_TAG=v1.2.3 docker compose -f docker-compose.prod.yml up -d
```

CI 工作流会在每个 SSH 部署步骤中自动设置这些环境变量，通常无需手动干预。

### 验证部署架构

确认容器运行的架构是否正确：

```bash
docker inspect <container_name> | grep Architecture
# 预期输出: "amd64" 或 "arm"
```
