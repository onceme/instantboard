---
version: 1.0
author: designer
date: 2026-06-23
status: draft
cross_refs: [architecture.md, database.md, security.md]
---

# InstantBoard 脚手架与DevOps设计

## 1. 目标

定义 InstantBoard 项目的完整脚手架结构、CI/CD 流程、Docker 环境配置和开发机隔离方案，确保开发环境不污染宿主机，生产部署自动化可靠。

## 2. 方案概述

采用 **Docker Compose 全容器化开发 + GitHub Actions CI/CD + .env 分层配置** 方案，开发/生产环境统一 Docker 编排，仅通过环境变量和配置差异区分。

## 3. 详细设计

### 3.1 项目目录结构

```mermaid
graph LR
    subgraph root["instantboard/"]
        subgraph github[".github/"]
            workflows["workflows/ (ci, cd-staging, cd-production)"]
            templates["ISSUE_TEMPLATE + PR_TEMPLATE"]
        end
        subgraph be["backend/"]
            app["app/"]
            app_main["main.py — FastAPI入口"]
            app_config["config/ (settings, logging)"]
            app_api["api/v1/ (auth, categories, sources, finance, tech, dashboard, stream, admin)"]
            app_models["models/ (user, tenant, category, source, item, watchlist, source_health, dashboard)"]
            app_schemas["schemas/ (auth, category, source, finance, tech, dashboard, common)"]
            app_services["services/ (finance, tech, dashboard, category, source, watchlist)"]
            app_collectors["collectors/ (base, rss, api, web, finance, tech)"]
            app_processors["processors/ (dedup, filter, categorizer, transformer)"]
            app_scheduler["scheduler/ (jobs, manager)"]
            app_sse["sse/ (event_router, manager)"]
            app_auth["auth/ (sso, jwt, middleware, tenant, rate_limit)"]
            app_db["db/ (postgres, redis, mongodb, session)"]
            alembic["alembic/ (env, versions)"]
            tests["tests/ (api, services, collectors, processors, integration)"]
            reqs["requirements/ (base, dev, prod)"]
            pyproject["pyproject.toml + Dockerfile + entrypoint.sh + manage.py"]
        end
        subgraph fe["frontend/"]
            fe_public["public/ (favicon, index.html)"]
            fe_src["src/"]
            fe_views["views/ (Finance, Tech, Dashboard, Settings, Login)"]
            fe_components["components/ (common, finance, tech, dashboard, settings)"]
            fe_stores["stores/ (auth, finance, tech, dashboard, settings, sse)"]
            fe_composables["composables/ (useSSE, useAuth, useFetch, useResponsive, useTheme, useWatchlist)"]
            fe_types["types/ (finance, tech, dashboard, common)"]
            fe_utils["utils/ (api, format, constants)"]
            fe_styles["styles/ (variables, global, mixins, dark, light)"]
            fe_config["package.json + tsconfig + vite.config + Dockerfile"]
        end
        subgraph docker_grp["docker/"]
            compose["docker-compose (dev, prod, test)"]
            nginx["nginx/ (dev, prod, ssl)"]
            dockerfiles["Dockerfiles (api, frontend, worker)"]
            db_config["配置 (postgres, redis, mongodb)"]
        end
        env_files[".env.example + .env + .gitignore"]
        makefile["Makefile + README.md"]
        subgraph docs_grp["docs/"]
            design["design/ (architecture, infrastructure, api, frontend, database, security, finance-tab, tech-tab, dashboard-tab, data-flow, data-sources, content-categories)"]
            api_docs["api/ — OpenAPI自动文档"]
        end
        scripts["scripts/ (setup-dev, run-tests, seed-data, generate-migration, clean-dev)"]
    end
```

**项目目录完整结构**:

```text
instantboard/
├── .github/
│   └── workflows/
│       ├── ci.yml                # PR 触发: lint + test
│       ├── cd-staging.yml        # 合到 staging 分支: build + deploy staging
│       └── cd-production.yml     # 合到 main 分支: build + deploy production
│   └── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI 入口 (app 创建、路由注册、lifespan)
│   │   ├── config/
│   │   │   ├── __init__.py
│   │   │   ├── settings.py       # Pydantic BaseSettings (从 .env 读取)
│   │   │   └── logging.py        # 日志配置
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py     # v1 路由汇总
│   │   │   │   ├── auth.py       # 认证端点
│   │   │   │   ├── categories.py # 分类 CRUD
│   │   │   │   ├── sources.py    # 数据源 CRUD
│   │   │   │   ├── finance.py    # 财经端点
│   │   │   │   ├── tech.py       # 科技端点
│   │   │   │   ├── dashboard.py  # Dashboard 端点
│   │   │   │   ├── stream.py     # SSE 端点
│   │   │   │   └── admin.py      # 管理端点 (多租户管理)
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── user.py           # 用户模型
│   │   │   ├── tenant.py         # 租户模型
│   │   │   ├── category.py       # 分类模型
│   │   │   ├── source.py         # 数据源模型
│   │   │   ├── item.py           # 信息条目模型
│   │   │   ├── watchlist.py      # 自选列表模型
│   │   │   ├── source_health.py  # 数据源健康模型
│   │   │   └── dashboard.py      # Dashboard 指标模型
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py           # 认证 Pydantic schema
│   │   │   ├── category.py       # 分类 schema
│   │   │   ├── source.py         # 数据源 schema
│   │   │   ├── finance.py        # 财经 schema
│   │   │   ├── tech.py           # 科技 schema
│   │   │   ├── dashboard.py      # Dashboard schema
│   │   │   └── common.py         # 公共 schema (分页、错误、响应包装)
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── finance_service.py
│   │   │   ├── tech_service.py
│   │   │   ├── dashboard_service.py
│   │   │   ├── category_service.py
│   │   │   ├── source_service.py
│   │   │   └── watchlist_service.py
│   │   ├── collectors/
│   │   │   ├── __init__.py
│   │   │   ├── base.py           # BaseCollector (抽象基类)
│   │   │   ├── rss_collector.py
│   │   │   ├── api_collector.py
│   │   │   ├── web_collector.py
│   │   │   ├── finance_collector.py
│   │   │   └── tech_collector.py
│   │   ├── processors/
│   │   │   ├── __init__.py
│   │   │   ├── dedup.py          # 去重处理器
│   │   │   ├── filter.py         # 关键词过滤
│   │   │   ├── categorizer.py    # 自动分类
│   │   │   └── transformer.py    # 数据格式转换
│   │   ├── scheduler/
│   │   │   ├── __init__.py
│   │   │   ├── jobs.py           # 定时任务定义
│   │   │   └── manager.py        # APScheduler/Celery 管理器
│   │   ├── sse/
│   │   │   ├── __init__.py
│   │   │   ├── event_router.py   # SSE 事件路由
│   │   │   └── manager.py        # SSE 连接管理
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── sso.py            # SSO 集成 (5种)
│   │   │   ├── jwt.py            # JWT 管理
│   │   │   ├── middleware.py     # 认证中间件
│   │   │   ├── tenant.py         # 多租户隔离中间件
│   │   │   └── rate_limit.py     # 限流中间件
│   │   └── db/
│   │       ├── __init__.py
│   │       ├── postgres.py       # PostgreSQL 连接管理
│   │       ├── redis.py          # Redis 连接管理
│   │       └ mongodb.py         # MongoDB 连接管理 (可选)
│   │       └ session.py         # SQLAlchemy session 管理
│   ├── alembic/
│   │   ├── env.py
│   │   ├── versions/
│   │   └ alembic.ini
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py           # pytest fixtures (db, client, auth)
│   │   ├── api/
│   │   │   ├── test_auth.py
│   │   │   ├── test_categories.py
│   │   │   ├── test_finance.py
│   │   │   ├── test_tech.py
│   │   │   ├── test_dashboard.py
│   │   │   └ test_stream.py
│   │   ├── services/
│   │   ├── collectors/
│   │   └ processors/
│   │   └ integration/
│   │   │   └ test_sse_integration.py
│   │   │   └ test_data_pipeline.py
│   ├── requirements/
│   │   ├── base.txt              # 核心依赖
│   │   ├── dev.txt               # 开发依赖 (pytest, ruff, etc.)
│   │   └ prod.txt               # 生产依赖 (gunicorn, etc.)
│   ├── pyproject.toml            # 项目元数据 + ruff + pytest 配置
│   ├── Dockerfile                # 多阶段构建
│   └ entrypoint.sh              # 容器入口脚本
│   └ manage.py                  # 管理命令 (类似 Django manage.py)
├── frontend/
│   ├── public/
│   │   ├── favicon.ico
│   │   └ index.html             # SPA 入口 HTML
│   ├── src/
│   │   ├── App.vue               # 根组件
│   │   ├── main.ts               # 入口 (createApp + plugins)
│   │   ├── router/
│   │   │   ├── index.ts          # Vue Router 路由定义
│   │   ├── views/
│   │   │   ├── FinanceView.vue
│   │   │   ├── TechView.vue
│   │   │   ├── DashboardView.vue
│   │   │   ├── SettingsView.vue
│   │   │   └ LoginView.vue
│   │   ├── components/
│   │   │   ├── common/
│   │   │   │   ├── AppHeader.vue
│   │   │   │   ├── AppSidebar.vue
│   │   │   │   ├── AppFooter.vue
│   │   │   │   ├── MessageCard.vue
│   │   │   │   ├── SearchBar.vue
│   │   │   │   ├── LoadingSpinner.vue
│   │   │   │   └ ErrorAlert.vue
│   │   │   ├── finance/
│   │   │   │   ├── MarketTicker.vue
│   │   │   │   ├── WatchlistPanel.vue
│   │   │   │   ├── StockDetail.vue
│   │   │   │   ├── FundDetail.vue
│   │   │   │   ├── NAVCalculator.vue
│   │   │   │   ├── MarketIndexCard.vue
│   │   │   │   ├── CommodityCard.vue
│   │   │   │   ├── FinanceSearch.vue
│   │   │   │    FinanceSubNav.vue
│   │   │   ├── tech/
│   │   │   │   ├── TopicFilter.vue
│   │   │   │   ├── NewsFeed.vue
│   │   │   │   ├── NewsCard.vue
│   │   │   │   ├── TopicTag.vue
│   │   │   │   ├── CategoryPanel.vue
│   │   │   ├── dashboard/
│   │   │   │   ├── HealthPanel.vue
│   │   │   │   ├── MetricsChart.vue
│   │   │   │   ├── ServiceStatus.vue
│   │   │   │   ├── SystemInfo.vue
│   │   │   │   ├── DataSourceHealth.vue
│   │   │   ├── settings/
│   │   │   │   ├── CategoryEditor.vue
│   │   │   │   ├── SourceEditor.vue
│   │   │   │   ├── ProfileSettings.vue
│   │   │   │   ├── ThemeToggle.vue
│   │   ├── stores/
│   │   │   ├── auth.ts            # 认证状态
│   │   │   ├── finance.ts         # 财经数据状态
│   │   │   ├── tech.ts            # 科技数据状态
│   │   │   ├── dashboard.ts       # Dashboard 状态
│   │   │   ├── settings.ts        # 设置状态
│   │   │   ├── sse.ts             # SSE 连接管理
│   │   ├── composables/
│   │   │   ├── useSSE.ts          # SSE 连接 composable
│   │   │   ├── useAuth.ts         # 认证 composable
│   │   │   ├── useFetch.ts        # REST fetch composable
│   │   │   ├── useResponsive.ts   # 响应式断点 composable
│   │   │   ├── useTheme.ts        # 主题 composable
│   │   │   ├── useWatchlist.ts    # 自选列表 composable
│   │   ├── types/
│   │   │   ├── finance.ts         # 财经 TypeScript 类型
│   │   │   ├── tech.ts            # 科技 TypeScript 类型
│   │   │   ├── dashboard.ts       # Dashboard 类型
│   │   │   ├── common.ts          # 公共类型
│   │   ├── utils/
│   │   │   ├── api.ts             # API 客户端配置
│   │   │   ├── format.ts          # 格式化工具
│   │   │   ├── constants.ts       # 常量定义
│   │   ├── styles/
│   │   │   ├── variables.scss     # CSS 变量 (主题色、断点)
│   │   │   ├── global.scss        # 全局样式
│   │   │   ├── mixins.scss        # SCSS mixins
│   │   │   ├── dark.scss          # 暗色主题
│   │   │   ├── light.scss         # 亮色主题
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── Dockerfile                 # 前端构建镜像
│   ├── .eslintrc.cjs
│   └ .prettierrc.json
├── docker/
│   ├── docker-compose.yml         # 开发环境编排
│   ├── docker-compose.prod.yml    # 生产环境编排 (overlay)
│   ├── docker-compose.test.yml    # 测试环境编排
│   ├── nginx/
│   │   ├── nginx.dev.conf         # 开发 Nginx 配置
│   │   ├── nginx.prod.conf        # 生产 Nginx 配置 (SSL + rate-limit)
│   │   └ ssl/                    # SSL 证书 (生产)
│   ├── api/
│   │   └ Dockerfile              # 后端多阶段构建
│   ├── frontend/
│   │   └ Dockerfile              # 前端构建+Nginx托管
│   ├── worker/
│   │   └ Dockerfile              # Celery Worker 镜像
│   ├── postgres/
│   │   ├── init.sql              # 初始化脚本
│   │    postgresql.conf         # PostgreSQL 配置
│   ├── redis/
│   │   ├── redis.conf            # Redis 配置
│   ├── mongodb/
│   │   ├── init.js               # MongoDB 初始化
│   │    mongod.conf             # MongoDB 配置
├── .env.example                   # 环境变量模板 (不含敏感值)
├── .env                           # 本地开发环境变量 (不入Git)
├── .gitignore
├── Makefile                       # 开发命令快捷脚本
├── README.md
├── docs/
│   ├── design/                    # 设计文档 (本目录)
│   │   ├── architecture.md
│   │   ├── infrastructure.md
│   │   ├── api.md
│   │   ├── frontend.md
│   │   ├── database.md
│   │   ├── security.md
│   │   ├── finance-tab.md
│   │   ├── tech-tab.md
│   │   ├── dashboard-tab.md
│   │   ├── data-flow.md
│   │   ├── data-sources.md
│   │   └ content-categories.md
│   └ api/                        # 自动生成的 OpenAPI 文档
└── scripts/
│   ├── setup-dev.sh              # 开发环境一键初始化
│   ├── run-tests.sh              # 运行所有测试
│   ├── seed-data.sh              # 数据库种子数据
│   └ generate-migration.sh       # 生成数据库迁移
│    clean-dev.sh                 # 清理开发容器和数据
```

### 3.2 Git 仓库初始化策略

**分支策略**: GitHub Flow (简化版)

```mermaid
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

**Git hooks** (via `pre-commit`):
- `pre-commit`: ruff lint + 格式化
- `pre-push`: 运行单元测试

### 3.3 GitHub Actions CI/CD 流程

#### CI 流程 (`ci.yml`) — PR 和 push 到任何分支触发

```yaml
name: CI
on: [push, pull_request]
jobs:
  lint-backend:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup-python 3.11
      - pip install requirements/dev.txt
      - ruff check app/
      - ruff format --check app/
  
  lint-frontend:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup-node 18
      - npm ci
      - eslint src/
      - prettier --check src/
  
  test-backend:
    runs-on: ubuntu-latest
    services:
      postgres: (image: postgres:15, env from .env.example)
      redis: (image: redis:7)
    steps:
      - checkout
      - setup-python 3.11
      - pip install requirements/dev.txt
      - pytest --cov=app --cov-report=xml
      - upload coverage to Codecov
  
  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup-node 18
      - npm ci
      - vitest run --coverage
      - upload coverage
  
  build-frontend:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup-node 18
      - npm ci
      - npm run build
      - verify dist/ exists
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

#### CD Production (`cd-production.yml`) — 合到 main 分支触发

```yaml
name: Deploy Production
on:
  push:
    branches: [main]
jobs:
  build-and-push:
    similar to staging, but tag: production-latest + git SHA
  deploy:
    steps:
      - SSH to production server
      - docker compose -f docker-compose.prod.yml pull
      - rolling deploy (backend first, then frontend)
      - health check (curl /api/v1/health)
      - run integration smoke tests
      - rollback on failure
```

### 3.4 Docker 开发环境配置

```yaml
# docker/docker-compose.yml (开发环境)
version: "3.8"
services:
  api:
    build:
      context: ../backend
      dockerfile: ../docker/api/Dockerfile
      target: development  # 开发阶段镜像
    ports:
      - "8000:8000"
    volumes:
      - ../backend/app:/app/app  # 热重载
    environment:
      - ENV=development
      - DATABASE_URL=postgresql://instantboard:devpass@postgres:5432/instantboard_dev
      - REDIS_URL=redis://redis:6379/0
      - MONGODB_URL=mongodb://mongodb:27017/instantboard_dev
      - SECRET_KEY=dev-secret-key-change-in-production
    depends_on:
      - postgres
      - redis
  
  postgres:
    image: postgres:15
    ports:
      - "5432:5432"  # 开发时可直接连接
    environment:
      - POSTGRES_USER=instantboard
      - POSTGRES_PASSWORD=devpass
      - POSTGRES_DB=instantboard_dev
    volumes:
      - postgres_dev_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_dev_data:/data
    command: redis-server /usr/local/etc/redis/redis.conf
  
  mongodb:
    image: mongo:6
    ports:
      - "27017:27017"
    environment:
      - MONGO_INITDB_ROOT_USERNAME=instantboard
      - MONGO_INITDB_ROOT_PASSWORD=devpass
    volumes:
      - mongodb_dev_data:/data/db
      - ./mongodb/init.js:/docker-entrypoint-initdb.d/init.js
    profiles: ["mongodb"]  # 初始版本默认不启动, 需显式启用: docker compose --profile mongodb up
  
  frontend:
    build:
      context: ../frontend
      dockerfile: ../docker/frontend/Dockerfile
      target: development
    ports:
      - "3000:3000"  # Vite dev server
    volumes:
      - ../frontend/src:/app/src
      - ../frontend/public:/app/public
    environment:
      - VITE_API_URL=http://localhost:8000
      - VITE_SSE_URL=http://localhost:8000
    depends_on:
      - api

volumes:
  postgres_dev_data:
  redis_dev_data:
  mongodb_dev_data:
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
    build:
      target: production  # 生产阶段镜像 (无 dev 依赖)
    ports: []  # 不对外暴露，仅 Nginx 内部访问
    environment:
      - ENV=production
      - DATABASE_URL=${PROD_DATABASE_URL}  # 从 .env.production
      - REDIS_URL=${PROD_REDIS_URL}
      - SECRET_KEY=${PROD_SECRET_KEY}
    restart: always
    healthcheck:
      test: curl -f http://localhost:8000/api/v1/health || exit 1
      interval: 30s
      timeout: 10s
      retries: 3

  worker:
    build:
      context: ../backend
      dockerfile: ../docker/worker/Dockerfile
    environment: (同 api 生产环境变量)
    restart: always
    depends_on:
      - redis
      - postgres

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
| Worker | 集成在 API 进程 (APScheduler) | 独立 Celery worker |
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
- docker `worker` 服务 healthcheck：由 `ps grep` 升级为在容器内用 `python -c` 解析
  `REDIS_URL`、读心跳键并比对 45s 新鲜度，失败 exit 1（interval 30s / retries 3 /
  start_period 60s）。进程假死但停止刷心跳的情况也会被判定不健康。

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

```
# .env.example (模板，入Git)
ENV=development
DATABASE_URL=postgresql://instantboard:devpass@postgres:5432/instantboard_dev
REDIS_URL=redis://redis:6379/0
MONGODB_URL=mongodb://instantboard:devpass@mongodb:27017/instantboard_dev
SECRET_KEY=change-this-in-production
JWT_SECRET=change-this-in-production
JWT_EXPIRATION_MINUTES=60

# SSO OAuth Credentials (需要用户自行申请)
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
AZURE_AD_CLIENT_ID=
AZURE_AD_CLIENT_SECRET=
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
APPLE_CLIENT_ID=
APPLE_TEAM_ID=
APPLE_KEY_ID=
APPLE_PRIVATE_KEY_PATH=
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=

# Financial Data API Keys
YAHOO_FINANCE_API_KEY=
ALPHA_VANTAGE_API_KEY=

# CORS
CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# SSE
SSE_HEARTBEAT_INTERVAL=30

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_BURST=10
```

**分层策略**:
- `.env.example`: 模板，入 Git，不含敏感值
- `.env`: 本地开发，不入 Git，开发者自行创建
- `.env.staging`: staging 服务器，不入 Git，通过 GitHub Secrets 注入
- `.env.production`: 生产服务器，不入 Git，通过 GitHub Secrets 注入

### 3.8 Makefile / 开发命令脚本

```makefile
.PHONY: help dev up down test lint build deploy clean seed

help:           ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk '...'

# === 开发环境 ===
dev:            ## 启动开发环境 (docker compose up)
	cd docker && docker compose up -d

up:             ## 启动所有服务
	cd docker && docker compose up -d

down:           ## 停止所有服务
	cd docker && docker compose down

logs:           ## 查看日志
	cd docker && docker compose logs -f api

# === 后端开发 ===
backend-shell:  ## 进入后端容器 shell
	cd docker && docker compose exec api bash

migrate:        ## 运行数据库迁移
	cd docker && docker compose exec api alembic upgrade head

makemigration:  ## 生成迁移文件
	cd docker && docker compose exec api alembic revision --autogenerate -m "$(msg)"

seed:           ## 填充种子数据
	cd docker && docker compose exec api python manage.py seed

# === 前端开发 ===
frontend-shell: ## 进入前端容器 shell
	cd docker && docker compose exec frontend bash

frontend-build: ## 构建前端生产版本
	cd frontend && npm run build

# === 测试 ===
test:           ## 运行所有测试
	cd docker && docker compose -f docker-compose.test.yml up --abort-on-container-exit

test-backend:   ## 运行后端测试
	cd docker && docker compose exec api pytest --cov

test-frontend:  ## 运行前端测试
	cd docker && docker compose exec frontend vitest run

# === Lint ===
lint:           ## 运行所有 lint
	cd backend && ruff check app/ && ruff format --check app/
	cd frontend && npx eslint src/ && npx prettier --check src/

lint-fix:       ## 自动修复 lint 问题
	cd backend && ruff check --fix app/ && ruff format app/
	cd frontend && npx eslint --fix src/ && npx prettier --write src/

# === 生产构建 ===
build:          ## 构建所有生产镜像
	cd docker && docker compose -f docker-compose.prod.yml build

# === 清理 ===
clean:          ## 清理开发容器和 volumes
	cd docker && docker compose down -v --rmi local

clean-data:     ## 仅清理数据 volumes
	cd docker && docker compose down -v
```

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Git 分支策略 | GitHub Flow | 项目初期简单够用，不需要 GitFlow 的复杂分支 |
| CI 触发时机 | 所有 push + PR | 尽早发现问题 |
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