---
version: 1.1
author: designer
date: 2026-08-24
status: reviewed
cross_refs: [architecture.md, database.md, security.md]
---

# InstantBoard 脚手架与DevOps设计

## 1. 目标

定义 InstantBoard 项目的完整脚手架结构、CI/CD 流程、Docker 环境配置和开发机隔离方案，确保开发环境不污染宿主机，生产部署自动化可靠。

## 2. 方案概述

采用 **Docker Compose 全容器化开发 + GitHub Actions CI/CD + .env 分层配置** 方案，开发/生产环境统一 Docker 编排，仅通过环境变量和配置差异区分。

## 3. 详细设计

### 3.1 项目目录结构

> 本目录树经 2026-08-24 审计后按仓库真实结构重写（原图约 25 处不符，已废弃旧版）。

```text
instantboard/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                # push:[main,staging,develop] + PR:[main,staging]
│   │   ├── cd-staging.yml        # 合入 staging 触发
│   │   └── cd-production.yml     # GitHub Release published 触发（非"合到 main"）
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI 入口 (app 创建、路由注册、lifespan)
│   │   ├── config.py             # ⚠️ 单文件 Pydantic BaseSettings（无 app/config/ 目录）
│   │   ├── dependencies.py       # FastAPI 依赖 (get_db / get_optional_token / ...)
│   │   ├── alembic/              # alembic.ini + env.py；⚠️ 无 versions/ 目录（无任何迁移脚本）
│   │   ├── api/
│   │   │   ├── router.py         # v1 路由汇总（注册 9 个模块, router.py:15-23）
│   │   │   └── v1/
│   │   │       ├── auth.py       # 认证端点
│   │   │       ├── categories.py # 分类 CRUD
│   │   │       ├── sources.py    # 数据源 CRUD
│   │   │       ├── finance.py    # 财经端点
│   │   │       ├── tech.py       # 科技端点
│   │   │       ├── dashboard.py  # Dashboard 端点
│   │   │       ├── health.py     # 健康检查
│   │   │       ├── sse.py        # SSE 端点（挂载于 /stream 前缀；⚠️ 不叫 stream.py）
│   │   │       └── admin.py      # 管理端点（多租户管理）
│   │   ├── models/
│   │   │   ├── base.py           # Declarative Base / 公共字段
│   │   │   ├── user.py           # 用户模型
│   │   │   ├── tenant.py         # 租户模型
│   │   │   ├── category.py       # 分类模型
│   │   │   ├── source.py         # 数据源模型（SourceHealth 亦在本文件；⚠️ 无 source_health.py）
│   │   │   ├── item.py           # 信息条目（含去重唯一约束）
│   │   │   ├── watchlist.py      # 自选列表
│   │   │   ├── finance.py        # FinanceQuote / FinanceSymbol / FundNAVEstimate
│   │   │   ├── dashboard.py      # Dashboard 指标
│   │   │   └── sse.py            # SSE 连接审计
│   │   ├── schemas/
│   │   │   ├── base.py           # 公共 schema（分页、错误、响应包装；⚠️ 不叫 common.py）
│   │   │   ├── auth.py / category.py / source.py
│   │   │   ├── finance.py / tech.py / dashboard.py
│   │   │   └── admin.py / item.py / sse.py
│   │   ├── services/             # ⚠️ 无 *_service 后缀
│   │   │   ├── auth.py           # 认证业务
│   │   │   ├── finance.py        # 财经业务
│   │   │   ├── tech.py           # 科技业务
│   │   │   ├── dashboard.py      # Dashboard 业务
│   │   │   ├── category.py       # 分类业务
│   │   │   ├── source.py         # 数据源业务（含源生命周期事件发布）
│   │   │   └── sse.py            # SSE 业务（连接管理 / 事件发布）
│   │   ├── collectors/
│   │   │   ├── base.py           # BaseCollector（collect + record_health）
│   │   │   ├── finance/          # yfinance / alpha_vantage / eastmoney / finnhub _collector.py
│   │   │   └── tech/             # rss / hackernews / arxiv _collector.py
│   │   ├── processors/
│   │   │   ├── base.py           # 处理器链
│   │   │   ├── dedup.py          # 去重（Redis MD5(title:url)）
│   │   │   ├── filter.py         # 关键词过滤
│   │   │   ├── categorizer.py    # 自动分类 / topic 提取
│   │   │   └── transformer.py    # 格式转换
│   │   ├── scheduler/            # ⚠️ 无 jobs.py
│   │   │   ├── manager.py        # AsyncSchedulerManager（APScheduler）
│   │   │   └── worker.py         # 生产独立进程入口（python -m app.scheduler.worker + Redis 心跳）
│   │   ├── core/                 # ⚠️ 认证/中间件/Redis 均在此（无 auth/、sse/ 目录）
│   │   │   ├── constants.py / exceptions.py
│   │   │   ├── security.py       # JWT 签发/校验（原"auth/jwt.py"）
│   │   │   ├── sso_handlers.py   # 5 种 SSO（原"auth/sso.py"）
│   │   │   ├── middleware.py     # CORS / 请求日志 / 限流中间件（空壳）
│   │   │   ├── redis.py          # Redis 客户端 + RedisKeys（不在 db/ 下）
│   │   │   └── sse_router.py     # SSE EventRouter（原"sse/event_router.py"）
│   │   ├── db/
│   │   │   ├── init_db.py        # create_tables（启动建表）
│   │   │   └── session.py        # SQLAlchemy session
│   │   └── tests/                # ⚠️ 空的脚手架残留（真实测试在 backend/tests/），建议清理
│   ├── tests/                    # 真实测试目录
│   │   ├── conftest.py
│   │   ├── unit/
│   │   └── integration/
│   ├── requirements/             # base.txt / dev.txt / prod.txt
│   ├── pyproject.toml            # 元数据 + ruff + pytest + mypy 配置
│   ├── Dockerfile                # ⚠️ 在 backend/ 根（多阶段: development / production）
│   └── entrypoint.sh             # 容器入口（含 create_tables / alembic -c 正确用法）
├── frontend/
│   ├── index.html                # ⚠️ SPA 入口在 frontend/ 根（非 public/）
│   ├── src/
│   │   ├── App.vue / main.ts
│   │   ├── api/                  # API 请求封装
│   │   ├── router/
│   │   ├── views/                # FinanceView / TechView / DashboardView / SettingsView /
│   │   │                         # LoginView / AdminLoginView / SSOCallbackView（共 7 个）
│   │   ├── components/
│   │   │   ├── layout/           # AppLayout / Header / Sidebar
│   │   │   ├── common/           # EmptyState / ErrorAlert / LoadingSpinner / ThemeToggle
│   │   │   ├── finance/          # FinanceGrid / QuoteCard / Watchlist / WatchlistMini /
│   │   │   │                     # SearchSymbols / MarketIndices / Commodities / FundNAV / FinanceSubNav
│   │   │   ├── tech/             # NewsFeed / NewsCard / TopicFilter / TopicTag / CategoryPanel / TechSubNav
│   │   │   ├── dashboard/        # HealthPanel / ServicesHealth / SystemStatus /
│   │   │   │                     # DataSourcesHealth / SSEStats / ChartWrapper
│   │   │   └── settings/         # CategoryEditor / SourceEditor / ProfileSettings
│   │   ├── stores/               # Pinia（auth / finance / tech / dashboard / settings / sse）
│   │   ├── composables/          # useAuth / useInfiniteScroll / useResponsive / useSSE / useTheme
│   │   ├── types/                # ⚠️ 单文件 types/index.ts
│   │   ├── utils/                # api.ts / sse.ts / format.ts 等
│   │   └── styles/               # variables.css + global.css（⚠️ 纯 CSS，无 SCSS）
│   ├── package.json / tsconfig*.json / vite.config.ts
│   ├── Dockerfile                # ⚠️ 在 frontend/ 根（多阶段: dev / prod）
│   ├── entrypoint.sh / nginx.conf
│   ├── eslint.config.js          # ⚠️ flat config（非 .eslintrc.cjs）
│   └── .prettierignore           # ⚠️ 无 .prettierrc.json
├── docker/
│   ├── docker-compose.yml        # 开发基础编排（刻意不发布任何宿主机端口）
│   ├── docker-compose.override.yml  # 开发覆盖：端口发布 + 热重载挂载
│   ├── docker-compose.prod.yml   # 生产编排
│   ├── docker-compose.test.yml
│   ├── nginx/
│   │   ├── nginx.conf            # 主配置（limit_req_zone 全被注释）
│   │   ├── conf.d/*.template     # http/https 模板
│   │   └── entrypoint.sh         # envsubst 渲染
│   ├── postgres/                 # init.sql（⚠️ 无 postgresql.conf）
│   └── redis/                    # redis.conf
├── scripts/                      # gen_admin_password_hash.py 等
├── .env.example                  # 178 行环境变量模板
├── Makefile
└── docs/
    ├── design/                   # 设计文档（本目录）
    └── deployment.md             # 部署指南（⚠️ 无 docs/api/ 目录）
```

> ⚠️ **未实现**：Alembic 迁移体系——`app/alembic/` 仅有 `alembic.ini + env.py`，无 `versions/` 目录、无任何迁移脚本；建表依赖 `entrypoint.sh` 的 `create_tables()`。

### 3.2 Git 仓库初始化策略

**分支策略**: GitHub Flow (简化版)

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
    main["main — 生产分支"]
    staging["staging — 预发布分支"]
    feature["feature/xxx — 功能分支"]
    bugfix["bugfix/xxx — 修复分支"]
    main --> staging
    staging --> feature
    staging --> bugfix
    feature -->|"PR → staging"| staging
    staging -->|"PR → main"| main
    bugfix -->|"PR → staging"| staging
    bugfix -->|"紧急PR → main"| main
```

- `main`: 生产分支，合入触发 production deploy
- `staging`: 预发布分支，合入触发 staging deploy
- `feature/*`: 功能分支，从 staging 创建，PR 合回 staging
- `bugfix/*`: 紧急修复，可从 main 创建直接合回 main + staging

**保护规则**:
- `main`: 必须通过 CI、必须 1 个 approve、禁止直接 push
- `staging`: 必须通过 CI

**Git hooks**:

> ⚠️ **未实现**：`pre-commit` 仅作为 dev 依赖声明，仓库内无 `.pre-commit-config.yaml`，pre-commit / pre-push 钩子均未实际安装。

### 3.3 GitHub Actions CI/CD 流程

#### CI 流程 (`ci.yml`) — push 到 main/staging/develop + PR 到 main/staging 触发（ci.yml:3-7）

```yaml
name: CI
on:
  push:
    branches: [main, staging, develop]
  pull_request:
    branches: [main, staging]
jobs:
  lint-backend:
    # setup-python 3.11 → pip install requirements/dev.txt + pip install -e .
    # ruff check app/
    # ruff format --check app/ tests/
    # mypy 软门禁: mypy app/ --ignore-missing-imports || true
    #   （当前约 425 个类型错误待清理；计划按模块修复后去掉 "|| true" 转硬门禁，
    #    期间不得新增错误 — ci.yml:42-47）
  lint-frontend:
    # setup-node 24 → npm ci（ci.yml:59）
    # eslint . --max-warnings=0 / prettier --check . / npm run type-check
  test-backend:
    services:
      postgres: postgres:15-alpine   # 与生产保持一致（ci.yml:83）
      redis: redis:7-alpine
    # ENV=test, SCHEDULER_ENABLED=false; pytest --cov → Codecov
  test-frontend:
    # Node 24 → npm run test
  build-frontend:
    needs: [lint-frontend, test-frontend]
    # npm run build → 校验 dist/ 存在
  build-docker:                     # ci.yml:185-214
    needs: [test-backend, build-frontend]
    # buildx: backend (target: production) + frontend (target: prod), push=false
```

#### CD Staging (`cd-staging.yml`) — 合到 staging 分支触发

```yaml
name: Deploy Staging
on:
  push:
    branches: [staging]
jobs:
  build-and-push:
    steps:
      - checkout
      - docker buildx (backend, frontend, worker)
      - push to GitHub Container Registry (GHCR)
      - tag: staging-latest
  deploy:
    steps:
      - SSH to staging server
      - docker compose -f docker-compose.prod.yml pull
      - docker compose -f docker-compose.prod.yml up -d
      - run smoke tests
```

#### CD Production (`cd-production.yml`) — GitHub Release 发布触发（`on: release: [published]`，cd-production.yml:3-5；**不是**"合到 main"）

```yaml
name: Deploy Production
on:
  release:
    types: [published]
jobs:
  build-and-push:
    # buildx + QEMU: linux/amd64,linux/arm/v7 → push GHCR
    # tag: production-latest + production-{sha} + {release_tag}
  deploy:
    # SSH: docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d
    # health check: /api/v1/health；滚动更新（先 api/worker，后 frontend/nginx）
```

### 3.4 Docker 开发环境配置

```yaml
# docker/docker-compose.yml（开发基础编排 — 刻意不发布任何宿主机端口）
services:
  api:
    build: { context: ../backend, dockerfile: Dockerfile, target: development }
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      - DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER:-instantboard}:${DB_PASSWORD:-devpass}@postgres:5432/${POSTGRES_DB:-instantboard_dev}
      - REDIS_URL=redis://redis:6379/0
      - ENV=development
    # 端口发布与热重载卷挂载全部在 override（见下）

  postgres:
    image: postgres:17     # ⚠️ 开发为 17（Debian 系，与既有数据目录兼容），生产为 15
    volumes: [postgres_dev_data, ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql]

  redis:
    image: redis:7-alpine
    command: redis-server /usr/local/etc/redis/redis.conf

  mongodb:
    image: mongo:6
    profiles: ["mongodb"]  # 默认不启动

  frontend:
    build: { context: ../frontend, dockerfile: Dockerfile, target: dev }  # ⚠️ 目标名为 dev
    command: npm run dev -- --host 0.0.0.0 --port 3000
```

```yaml
# docker/docker-compose.override.yml（开发覆盖）
# ⚠️ 仅在 `docker compose` 不带 -f 时自动加载；显式 -f（如生产合并）时不加载。
# 所有宿主机端口发布与热重载挂载都放在这里——因为 Compose 对列表字段按拼接合并，
# 基础文件无法"取消发布"端口/卷，故基础文件刻意无端口、无挂载。
services:
  postgres:   { ports: ["${POSTGRES_PORT:-5432}:5432"] }
  redis:      { ports: ["${REDIS_PORT:-6379}:6379"] }
  mongodb:    { ports: ["${MONGODB_PORT:-27017}:27017"] }   # 仅 profile 启用时生效
  api:
    ports: ["${API_PORT:-8000}:8000"]
    volumes: [../backend/app:/app/app, ../backend/entrypoint.sh:/app/entrypoint.sh]
  frontend:
    ports:
      - "${FRONTEND_PORT:-3000}:3000"          # Vite dev server
      - "${FRONTEND_STATIC_PORT:-3001}:80"     # 可选：静态产物调试
    volumes: [../frontend/src:/app/src, ../frontend/public:/app/public, ../frontend/vite.config.ts:/app/vite.config.ts]
```

### 3.5 Docker 生产部署配置

```yaml
# docker/docker-compose.prod.yml (生产 overlay)
version: "3.8"
services:
  nginx:
    image: nginx:1.25-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.prod.conf:/etc/nginx/nginx.conf
      - ./nginx/ssl:/etc/nginx/ssl
      - frontend_static:/usr/share/nginx/html
    depends_on:
      - api

  api:
    image: ${IMAGE_REGISTRY:-ghcr.io}/${IMAGE_PREFIX:-onceme/instantboard}-api:${IMAGE_TAG:-latest}
    build: { context: ../backend, dockerfile: Dockerfile, target: production }
    environment:
      - ENV=production
      # environment 优先于 env_file，覆盖 .env 中的 SCHEDULER_ENABLED=true：
      # 生产由独立 worker 负责调度，避免双调度器重复采集
      - SCHEDULER_ENABLED=false
      - DATABASE_URL=${PROD_DATABASE_URL:?...}   # :? 强制要求，缺失则启动失败
      - REDIS_URL=${PROD_REDIS_URL:?...}
      - SECRET_KEY=${PROD_SECRET_KEY:?...}
    command: gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker ...

  worker:
    # ⚠️ 复用 backend Dockerfile（target: production），无独立 worker 镜像
    # （docker-compose.prod.yml:97-117）
    image: ${IMAGE_REGISTRY:-ghcr.io}/${IMAGE_PREFIX:-onceme/instantboard}-api:${IMAGE_TAG:-latest}
    environment: [ENV=production, 同 api 的 :? 密钥三项]
    command: python -m app.scheduler.worker      # APScheduler 独立进程 + Redis 心跳
    # healthcheck: 容器内读心跳键校验 45s 新鲜度

  postgres:
    ports: []  # 不对外暴露
    volumes:
      - postgres_prod_data:/var/lib/postgresql/data
    restart: always

  redis:
    ports: []  # 不对外暴露
    command: redis-server /usr/local/etc/redis/redis.conf --requirepass ${PROD_REDIS_PASSWORD}
    restart: always

  mongodb:
    ports: []  # 不对外暴露
    restart: always
    profiles: ["mongodb"]  # 按需启用: docker compose --profile mongodb up
```

**开发 vs 生产 Docker 差异总结**:

| 项目 | 开发环境 | 生产环境 |
|------|---------|---------|
| API 端口 | 8000 直接暴露 | 仅内部，Nginx 代理 |
| 数据库端口 | 直接暴露（可本地调试） | 不暴露 |
| 前端 | Vite dev server (3000) | Nginx 托管构建产物 |
| 热重载 | volume mount + --reload | 无，构建后静态 |
| Worker | 内嵌 API 进程（APScheduler，SCHEDULER_ENABLED=true） | 独立进程：复用 backend 镜像 + `command: python -m app.scheduler.worker`（api 侧 SCHEDULER_ENABLED=false） |
| 数据库镜像 | postgres:17（开发） | postgres:15-alpine（生产，与既有数据目录锁定版本） |
| Nginx | 不使用 | 必须使用 (SSL + rate-limit) |
| MongoDB | 按需 profile 启动 | 按需 profile 启动 |
| Health check | 无 | 必须 |
| Restart policy | 无 | always |
| 密码 | 开发固定密码 | .env.production 读取 |

> **设计变更（2026-07）**：移除了 `!reset` 和 `!override` YAML 标签以兼容 V1 `docker-compose`。
> 前端服务现在显式使用 `command: ["/entrypoint.sh"]` 替代 `!reset null`，
> 因为 Docker Compose 默认的序列合并语义就是替换，`!override` 实际冗余。
> 同时移除了 `deploy.resources.reservations.cpus`（V1 不支持）。
> 本节中仍保留的 `!reset`/`!override` 示例仅供历史参考，已不再使用。

#### 多架构构建策略（2026-07）

项目 CI 使用 buildx + QEMU 构建 `linux/amd64,linux/arm/v7` manifest list。

**前端 Dockerfile**：`builder` 阶段使用 `--platform=$BUILDPLATFORM` 强制在 host 平台（amd64）运行，
因为 `node:24-alpine` 没有 arm/v7 官方镜像；后续 `COPY --from=builder` 跨平台复制静态资源，
静态资源架构无关，arm/v7 nginx 镜像正常服务。

**Backend Dockerfile**：`builder` 阶段安装 `build-essential`/`libssl-dev`/`libffi-dev`
作为 Python C 扩展缺少 arm/v7 wheel 时的源码编译回退。

**MongoDB**：`mongo:6` 不支持 arm/v7，通过 `profiles: ["mongodb"]` gate 隔离，不影响默认部署。
arm/v7 服务器上启用 `--profile mongodb` 将导致 `no matching manifest` 错误。

**生产镜像地址**：`docker-compose.prod.yml` 使用 `${IMAGE_REGISTRY:-ghcr.io}/${IMAGE_PREFIX:-onceme/instantboard}-{api|frontend}:${IMAGE_TAG:-latest}`，
CI 工作流在各 SSH 部署步骤中设置这三个环境变量，本地手动部署可用 `IMAGE_TAG=v1.2.3 docker compose ... up -d` 覆盖。

#### Worker 心跳机制（2026-08）

**背景**：生产部署中 api 进程以 `SCHEDULER_ENABLED=false` 运行，采集调度器跑在独立的
worker 容器里（`python -m app.scheduler.worker`）。仪表盘的服务健康检查原先只读 api
进程内嵌的调度器单例（`scheduler_manager._running` + `get_jobs_status()`），在 prod
下该单例从未启动，导致 scheduler 恒报 down；`/dashboard/scheduler` 与快照归档
（`scheduler_jobs_active`）同样读到空数据恒为 0。同时 worker 自身没有任何对外可观测
状态（事件监听器悄悄退出无人知晓，docker healthcheck 只做 `ps grep`）。

**方案（方案 1：worker 心跳写 Redis，api 读之）**：

| 项目 | 约定 |
|------|------|
| 键名 | `scheduler:worker:heartbeat`（`RedisKeys.WORKER_HEARTBEAT`） |
| 值 | JSON 字符串（见下方 payload） |
| 写入方 | worker 后台 asyncio 任务 `heartbeat_loop`，每 **15s** 写一次，启动后立即写第一次 |
| TTL | **45s**（`RedisKeys.WORKER_HEARTBEAT_TTL`，= 3 × 写入间隔） |
| 清理 | worker 优雅退出时尽力 `DEL` 该键（失败不阻塞关闭，TTL 兜底过期） |

**payload 字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | string | 写入时刻，ISO-8601 UTC |
| `pid` | int | worker 进程号，便于排查多实例/僵尸进程 |
| `scheduler_running` | bool | worker 内 APScheduler 是否在运行 |
| `jobs_total` / `jobs_running` / `jobs_paused` | int | 采集任务总数 / 运行中 / 已暂停 |
| `event_listener_subscribed` | bool | source 事件 Pub/Sub 监听器是否存活；监听器退出会立即体现在该字段上 |

**健康判定语义**（api 侧，仅当 `SCHEDULER_ENABLED=false` 的 prod 模式生效；
开发内嵌模式仍直接查询本进程调度器单例）：

- 心跳存在且 `now - timestamp < 45s`（fresh）→ scheduler **healthy**，details 携带
  jobs 统计与 `last_heartbeat`；
- 心跳过期（stale）或缺失（missing/已过期删除）→ scheduler **down**，details 携带
  `last_heartbeat`（缺失时为 never）；
- Redis 本身不可用导致读不到心跳 → 同样 down，但 details 表述联动 Redis 服务状态
  （`redis unavailable: ...`），不把锅扣在 worker 上。

**消费方**：

- `get_services_health`（仪表盘服务健康表）：prod 模式读心跳判定 scheduler 状态；
- `/api/v1/dashboard/scheduler`（`get_scheduler_status`）：prod 模式 job 列表为空，
  total/running/paused 数量取心跳值，并返回 `last_heartbeat`；
- `archive_snapshot`：prod 模式 `scheduler_jobs_active` 取心跳 `jobs_running`，
  消除恒 0 污染；
- docker `worker` 服务 healthcheck：由 `ps grep` 升级为探测 Redis 中的**真实心跳
  键**，利用 TTL 语义判定新鲜度：worker 每 **15s** 重写心跳键、TTL **45s**，因此
  **键存在 ⇔ 心跳新鲜（≤45s）**；worker 死亡或假死后键在 45s 内自然过期。探测
  命令为 `redis-cli -u <url> --no-auth-warning exists scheduler:worker:heartbeat`
  （输出 `1` → 健康；输出 `0`、连接失败或无输出 → 不健康，失败时向 stderr 打印
  一行原因，便于从 `docker inspect ...State.Health` 定位）。参数：**interval 30s /
  timeout 10s** / retries 3 / start_period 60s。Redis 不可达同样判不健康，覆盖原
  脚本的连接检查语义；连接参数取应用自身使用的 `$REDIS_URL`（`app/core/redis.py`），
  但需做一次 URL 归一化：项目标准格式 `redis://:password@host`（空用户名）会被
  redis-cli 当成真实 ACL 用户导致 AUTH 失败（redis-cli 8 实测 WRONGPASS，而
  redis-py 连接正常），故 healthcheck 先将其改写为 `redis://password@host` 再
  传给 `-u`。`redis-tools` 已在 production 镜像中（backend Dockerfile 运行阶段，
  entrypoint.sh 的 Redis 等待循环也在用）。
  - **为什么不用 Python 探针（2026-08 演进记录）**：第一版在容器内用 `python -c`
    读键并解析时间戳。staging 是树莓派 2（Cortex-A7 @ 900MHz / 1GB RAM，worker
    限制 `cpus: 0.5`），CPython + redis-py 冷启动超过任何合理的健康检查超时，被
    Docker 逐次杀掉（`Health check exceeded timeout (10s)`，FailingStreak=180+，
    同栈其余 5 个容器的 curl/内置工具探针 300–400ms 全部健康）；中途把 timeout
    放宽到 30s、interval 放宽到 60s 仍属勉强，且每次冷启动都挤占 worker 的 0.5
    CPU 配额、干扰采集任务。故改为 redis-cli 探针：C 二进制毫秒级启动，新鲜度
    判定完全交给 TTL，与原来的时间戳比较（< 45s）语义等价。

**心跳任务的可靠性约定**：心跳写入的任何异常都被捕获并记日志，绝不杀死 worker 进程；
心跳键带 TTL，崩溃的 worker 会在 45s 内自然转为 stale/down，无需外部清理。

**部署提示**：本机制只涉及 backend 代码与 compose healthcheck，需重建
**api 与 worker 两个镜像**（同一 backend Dockerfile）后重启生效；无数据库迁移、
无前端改动、无新增依赖。

### 3.6 开发机环境隔离方案

所有服务运行在 Docker 容器中，宿主机仅需安装:
- Docker Engine 24+
- Docker Compose V2+
- Git

**隔离要点**:
- 数据库/Redis/MongoDB 数据存储在 Docker volumes 中，不污染宿主机
- 端口仅映射到 `localhost`，不暴露到局域网
- `.env` 文件不入 Git (`.gitignore` 包含 `.env`)
- 前端开发在容器内 Vite dev server，宿主机浏览器访问 `http://localhost:3000`
- 后端 API 容器内 uvicorn，宿主机可 `http://localhost:8000/api/v1/docs` 查看 Swagger

### 3.7 .env 配置管理

```text
# .env.example 实际为 178 行模板（要点，键名与文件一致）:

# --- General ---
ENV / LOG_LEVEL

# --- PostgreSQL ---
DATABASE_URL=postgresql+asyncpg://instantboard:devpass@localhost:5432/instantboard_dev
  # 模板 host 为 localhost（本机直连口径；容器内由 compose 覆盖为 postgres 服务名）
DB_PASSWORD / POSTGRES_USER / POSTGRES_DB / POSTGRES_PORT
DATABASE_POOL_SIZE=20 / DATABASE_MAX_OVERFLOW=10 / DATABASE_POOL_RECYCLE=3600

# --- Redis ---
REDIS_URL / REDIS_PORT / REDIS_PASSWORD / REDIS_MAX_MEMORY=512mb

# --- MongoDB（初始版本不启用）---
MONGODB_URL / MONGO_USER / MONGO_PASSWORD / MONGODB_PORT

# --- JWT ---
JWT_SECRET          # ≥32 字符；staging/production 下占位符或过短将拒绝启动
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60    # ⚠️ 实际键名（非 JWT_EXPIRATION_MINUTES）
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# --- SSO ---
ENABLED_SSO_PROVIDERS=google,github + 5 家提供商凭据（含 AZURE_AD_TENANT_ID）

# --- 本地管理员登录 ---
ADMIN_EMAIL / ADMIN_PASSWORD_HASH（bcrypt，make gen-admin-hash 生成）
ADMIN_PASSWORD（仅非生产环境的明文便捷项）

# --- Financial API Keys ---
YAHOO_FINANCE_API_KEY / ALPHA_VANTAGE_API_KEY / FINNHUB_API_KEY / FINNHUB_API_KEYS

# --- CORS / SSE / 限流 ---
CORS_ORIGINS / SSE_HEARTBEAT_INTERVAL=30 / RATE_LIMIT_PER_MINUTE / RATE_LIMIT_BURST
  # ⚠️ 限流配置项目前无消费者（见 architecture.md）

# --- 租户默认 ---
DEFAULT_TENANT_SLUG / DEFAULT_TENANT_NAME

# --- 调度器 / 端口 ---
SCHEDULER_ENABLED=true / API_PORT=8000 / FRONTEND_PORT=3000 / VITE_API_URL / VITE_SSE_URL

# --- Nginx / SSL ---
SERVER_NAME / HTTP_PORT / HTTPS_PORT / ENABLE_HTTPS / PUBLIC_BASE_URL /
HSTS_MAX_AGE / SSL_CERT_DIR / SSL_CERT_FILE（出站 CA bundle，仅非 Docker 运行需要）

# --- 生产附加（PROD_*）---
PROD_DATABASE_URL / PROD_REDIS_URL / PROD_SECRET_KEY / PROD_JWT_SECRET /
PROD_DB_PASSWORD / PROD_REDIS_PASSWORD / PROD_POSTGRES_DB / PROD_POSTGRES_USER /
PROD_MONGO_USER / PROD_MONGO_PASSWORD / PROD_DOMAIN / PROD_CORS_ORIGINS
```

**分层策略**:
- `.env.example`: 模板，入 Git，不含敏感值
- `.env`: 本地开发，不入 Git，开发者自行创建
- `.env.staging`: staging 服务器，不入 Git，通过 GitHub Secrets 注入
- `.env.production`: 生产服务器，不入 Git，通过 GitHub Secrets 注入

### 3.8 Makefile / 开发命令脚本

```makefile
# 实际 Makefile 要点（199 行，以仓库文件为准）

COMPOSE      := cd docker && docker compose
COMPOSE_PROD := cd docker && docker compose -f docker-compose.yml -f docker-compose.prod.yml
# ⚠️ COMPOSE_PROD 不带 --env-file，${PROD_*} 插值仅读 docker/ 目录下的 .env
#（详见 deployment.md「make prod-up 的 .env 可见性问题」）

# 开发环境
dev / up / down            # dev/up 会自动从 .env.example 复制 .env
logs / logs-api / logs-worker / logs-frontend / logs-db
backend-shell / frontend-shell / db-shell / redis-shell

# 测试
test:                       # = test-backend + test-frontend（⚠️ compose 测试是独立的
test-docker:                #   docker-compose.test.yml 目标）
test-unit / test-integration / test-e2e   # ⚠️ test-e2e 目前无用例
test-backend:               # 容器内 pytest（容器不可用时回退宿主机）
test-frontend:              # npm run test

# 代码质量
lint / lint-fix / format

# 构建
build / build-api / build-frontend   # ⚠️ 均为构建 Docker 镜像（无宿主机 npm 构建目标）
build-prod

# 数据库
migrate / makemigration
# ⚠️ 已知问题: 二者均未带 -c app/alembic/alembic.ini，从容器工作目录执行会找不到配置；
# 正确用法参见 backend/entrypoint.sh:46
seed:                       # 实际执行 python -c "from app.db.init_db import init_db; ..."（Makefile:143-145，非 manage.py）
gen-admin-hash              # 生成 ADMIN_PASSWORD_HASH（PASS=... 或交互式）

# 清理 / 生产 / 无 Docker
clean / clean-data / reset-db
prod-up / prod-down / prod-logs
local-dev / local-dev-backend / local-dev-frontend
```

> ⚠️ **未实现**：`frontend-build`（npm 生产构建）目标不存在——`build-frontend` 是构建前端 **Docker 镜像**；`test-e2e` 无任何用例；git hooks / Alembic 迁移脚本见上文对应标注。

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Git 分支策略 | GitHub Flow | 项目初期简单够用，不需要 GitFlow 的复杂分支 |
| CI 触发时机 | push 到 main/staging/develop + PR 到 main/staging | 尽早发现问题，同时避免对临时分支重复跑全量流水线 |
| 开发环境 | Docker Compose 全容器 | 避免污染开发机，新人一键启动 |
| 前端开发服务 | 容器内 Vite dev server | 端口映射到宿主机，开发体验不变 |
| MongoDB 启动策略 | 生产环境用 profile 按需 | 不必强制启动，节省资源 |
| 配置管理 | .env 分层 + GitHub Secrets | 安全且灵活 |

## 5. 边界情况

- **Docker 不可用**: Makefile 提供 `local-dev` 目标，直接在本机运行 (需要 Python/Node/PostgreSQL/Redis)
- **端口冲突**: `.env` 中可配置端口映射，默认使用 3000/8000/5432/6379
- **数据丢失**: Docker volumes 持久化，`make clean` 才删除；生产环境使用独立 volume 或云存储
- **首次启动慢**: 提供预构建镜像推送到 GHCR，`docker compose pull` 快速启动

## 6. 与其他模块的依赖

- → [architecture.md](architecture.md): 架构定义决定容器编排结构
- → [database.md](database.md): 数据库配置影响 Docker compose 服务定义
- → [security.md](security.md): .env 中的密钥管理、Nginx SSL 配置