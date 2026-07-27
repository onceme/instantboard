# InstantBoard 部署指南

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
- **安全校验**：未在 `ENABLED_SSO_PROVIDERS` 中的提供商调用登录接口时，返回 `400 PROVIDER_NOT_ENABLED`
- **前端集成**：前端通过 `GET /api/v1/auth/sso/providers` 接口获取启用列表，未配置的提供商登录按钮自动隐藏

## JWT 配置

```bash
SECRET_KEY=your-strong-secret-key-at-least-32-chars
JWT_SECRET=your-jwt-secret-at-least-32-chars
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

## 完整生产环境 .env 示例

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
SECRET_KEY=<strong-random-key>
JWT_SECRET=<strong-random-key>

# --- CORS ---
CORS_ORIGINS=https://your-domain.com

# --- Financial Data ---
ALPHA_VANTAGE_API_KEY=your_key
```

## Docker Compose 部署

参考 [快速开始](../README.md#快速开始) 章节和 [infrastructure.md](design/infrastructure.md) 了解完整的 Docker 编排方案。

```bash
# 开发环境
make dev

# 生产环境
make build-prod
make prod-up
```

Makefile 和所有 shell 脚本均自动检测 `docker compose`（V2 插件）与 `docker-compose`（V1 独立版），两者均可使用，无需手动配置。

### 架构支持

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
