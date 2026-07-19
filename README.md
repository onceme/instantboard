# InstantBoard — 实时信息聚合消息板

> 基于 FastAPI + Vue 3 + SSE 的实时信息聚合平台，汇集全球金融市场数据与前沿科技资讯。

[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.4-42b883.svg)](https://vuejs.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docker.com)

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术架构](#技术架构)
- [快速开始](#快速开始)
  - [环境要求](#环境要求)
  - [一键启动（Docker）](#一键启动docker)
  - [本地开发（无 Docker）](#本地开发无-docker)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [功能详解](#功能详解)
  - [财经模块 (Finance)](#财经模块-finance)
  - [科技模块 (Tech)](#科技模块-tech)
  - [监控仪表盘 (Dashboard)](#监控仪表盘-dashboard)
  - [SSO 认证](#sso-认证)
  - [实时推送 (SSE)](#实时推送-sse)
- [API 文档](#api-文档)
- [常用命令](#常用命令)
- [测试](#测试)
- [生产部署](#生产部署)
- [CI/CD](#cicd)
- [数据源](#数据源)
- [多租户](#多租户)
- [常见问题](#常见问题)
- [开发指南](#开发指南)
- [许可证](#许可证)

---

## 项目简介

InstantBoard 是一个**实时信息聚合消息板**服务，采用前后端分离架构，通过 SSE (Server-Sent Events) 实时推送数据到浏览器。

**两大核心内容板块**：

| 板块 | 内容 | 数据来源 |
|------|------|---------|
| 📈 **财经 (Finance)** | 股票/基金搜索、自选关注列表、基金 NAV 实时估值、全球市场指数、大宗商品期货 | Yahoo Finance、Alpha Vantage、东方财富 |
| 🔬 **科技 (Tech)** | 机器人、AI、大规模嵌入式、太空科技四大领域资讯聚合 | RSS 20+ 源、HackerNews、ArXiv、SpaceNews |

---

## 核心功能

- 🔄 **SSE 实时推送** — 服务端主动推送行情和新闻更新，浏览器零延迟刷新
- 📊 **自选关注列表** — 自定义股票/基金关注列表，实时涨跌幅追踪
- 💰 **基金 NAV 估值** — 基于跟踪指数的 ETF 实时净值估算（指数跟踪法）
- 🌍 **全球市场指数** — S&P 500、纳斯达克、上证、恒生、日经等 13+ 指数
- 🛢️ **大宗商品** — 黄金、原油、白银、天然气等 7+ 期货品种
- 🏷️ **话题标签过滤** — 三级标签体系（领域→子分类→话题），支持跨领域筛选
- 📡 **20+ 数据源** — RSS/API/网页抓取三种采集方式，自动故障转移
- 🔐 **5 种 SSO 登录** — 默认启用 Google / GitHub，可按需启用 Azure AD / Apple / Facebook
- 🏢 **多租户架构** — PostgreSQL 行级隔离 (RLS)，租户独立配置
- 🛡️ **多层安全** — JWT 双 Token + Nginx 限流 + CSP + CORS
- 📱 **响应式设计** — Tailwind CSS 适配桌面/平板/手机
- 🌓 **深/浅色主题** — 运行时切换，支持涨跌颜色配置
- 📈 **轻量监控仪表盘** — 系统/CPU/内存、数据源健康、SSE 连接统计

---

## 技术架构

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue 3.4 + TypeScript + Vite 5 | Composition API, Pinia 状态管理, Tailwind CSS |
| **后端** | Python 3.11+ / FastAPI | Async 全链路, SQLAlchemy 2.0 ORM, APScheduler |
| **实时推送** | SSE + Redis Pub/Sub | 单向服务端推送, 30s 心跳, 自动重连 |
| **数据库** | PostgreSQL 15 | JSONB 支持, 行级安全 (RLS), 11 张核心表 |
| **缓存** | Redis 7 | 行情缓存 / 会话 / Pub/Sub / 限流 |
| **反向代理** | Nginx 1.25 | SSL, 限流, 静态资源, API 代理 |
| **容器** | Docker Compose | 开发/生产统一编排 |
| **CI/CD** | GitHub Actions | Lint → Test → Build → Deploy |

### 架构图

```mermaid
graph TB
    subgraph Browser["🖥️ 浏览器 (Vue 3 SPA)"]
        direction LR
        Finance["📈 Finance"] ~~~ Tech["🔬 Tech"] ~~~ Dashboard["📊 Dashboard"]
    end

    Browser -->|"REST API 请求"| Nginx
    Browser -.->|"SSE 实时流"| Nginx

    subgraph Nginx["🔒 Nginx 反向代理 / SSL / 限流"]
    end

    Nginx --> App

    subgraph App["⚡ FastAPI Application (Python 3.11+)"]
        direction TB
        subgraph Modules["核心模块"]
            direction LR
            API["🔌 API Routes<br/>9 个端点模块"]
            Service["🧠 Service Layer<br/>7 个业务服务"]
            Collector["📡 Collector Engine<br/>7 个采集器"]
            Scheduler["⏰ Scheduler<br/>APScheduler"]
        end
        SSE["📡 SSE EventRouter + Redis Pub/Sub<br/>8 种事件类型 · 5 个频道 · 30s 心跳"]
    end

    App -->|"SQLAlchemy 2.0<br/>JSONB · RLS"| PG
    App -->|"hiredis · aioredis<br/>缓存 · Pub/Sub · 限流"| Redis

    PG[("🐘 PostgreSQL 15<br/>11 张核心表")]
    Redis[("🔴 Redis 7<br/>12 种 Key 模式")]

    style Browser fill:#e3f2fd,stroke:#1565c0,color:#000
    style Nginx fill:#f3e5f5,stroke:#7b1fa2,color:#000
    style App fill:#e8f5e9,stroke:#2e7d32,color:#000
    style Modules fill:#fff8e1,stroke:#f9a825,color:#000
    style SSE fill:#fce4ec,stroke:#c62828,color:#000
    style PG fill:#e8eaf6,stroke:#283593,color:#000
    style Redis fill:#ffebee,stroke:#b71c1c,color:#000
```

---

## 快速开始

### 环境要求

| 工具 | 最低版本 | 说明 |
|------|---------|------|
| Docker Engine | 24+ | 容器运行时 |
| Docker Compose | V2+ | 服务编排 |
| Git | 2.0+ | 代码拉取 |

> 💡 如果不使用 Docker，需要：Python 3.11+、Node.js 18+、PostgreSQL 15、Redis 7

### 一键启动（Docker）

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/instantboard.git
cd instantboard

# 2. 运行自动安装脚本
bash scripts/setup-dev.sh
```

安装脚本会自动完成：
- ✅ 检测 Docker/Git 环境
- ✅ 复制 `.env.example` → `.env`
- ✅ 构建 Docker 镜像并启动所有服务

**启动完成后访问**：

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/api/v1/docs |
| API 文档 (ReDoc) | http://localhost:8000/api/v1/redoc |

**Docker Compose 服务清单**（开发环境）：

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| `postgres` | postgres:15-alpine | 5432 | 主数据库 |
| `redis` | redis:7-alpine | 6379 | 缓存 + Pub/Sub |
| `api` | 自定义 Python 3.11-slim | 8000 | FastAPI (热重载) |
| `frontend` | 自定义 Node 18-alpine | 3000 | Vite dev server (HMR) |

> MongoDB 6 默认不启动，可通过 `--profile mongodb` 按需启用。

### 本地开发（无 Docker）

如果不想使用 Docker，也可以直接在本地运行：

```bash
# 1. 后端
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements/base.txt -r requirements/dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2. 前端（另一个终端）
cd frontend
npm install
npm run dev
```

> ⚠️ 本地模式需要预先安装并配置 PostgreSQL 和 Redis。

也可以使用 Makefile 快捷命令：

```bash
make local-dev-backend   # 启动后端
make local-dev-frontend  # 启动前端
make local-dev           # 同时启动前后端
```

---

## 配置说明

### 环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

**关键配置分组**：

#### 基础配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENV` | `development` | 运行环境 (development/production) |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `API_PORT` | `8000` | 后端 API 端口 |
| `FRONTEND_PORT` | `3000` | 前端开发端口 |

#### 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL 连接串 |
| `DB_PASSWORD` | `devpass` | 数据库密码 |
| `DATABASE_POOL_SIZE` | `20` | 连接池大小 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |

#### 认证与 JWT

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECRET_KEY` | *(需修改)* | 应用密钥（生产必须替换！） |
| `JWT_SECRET` | *(需修改)* | JWT 签名密钥 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access Token 有效期 |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh Token 有效期 |

#### SSO OAuth（按需配置）

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

#### 财经数据 API

| 变量 | 说明 |
|------|------|
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API Key (备用数据源) |
| `FINNHUB_API_KEY` | Finnhub API Key (可选) |

> yfinance (Yahoo Finance) 为主数据源，无需 API Key。

#### SSE & 限流

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SSE_HEARTBEAT_INTERVAL` | `30` | SSE 心跳间隔（秒） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每分钟请求限制 |
| `RATE_LIMIT_BURST` | `10` | 突发请求量 |

---

## 项目结构

```
instantboard/
├── .env.example              # 环境变量模板
├── Makefile                  # 30+ 开发命令
├── LICENSE                   # GNU GPLv3
│
├── backend/                  # Python/FastAPI 后端
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── config.py         # Pydantic Settings
│   │   ├── dependencies.py   # FastAPI 依赖注入
│   │   ├── api/v1/           # 9 个 API 路由模块
│   │   │   ├── auth.py       # 认证 (SSO)
│   │   │   ├── finance.py    # 财经数据
│   │   │   ├── tech.py       # 科技资讯
│   │   │   ├── dashboard.py  # 监控仪表盘
│   │   │   ├── categories.py # 分类管理
│   │   │   ├── sources.py    # 数据源管理
│   │   │   ├── sse.py        # SSE 推送
│   │   │   ├── health.py     # 健康检查
│   │   │   └── admin.py      # 管理端点
│   │   ├── models/           # 11 个 SQLAlchemy 模型
│   │   ├── schemas/          # Pydantic 请求/响应 schema
│   │   ├── services/         # 7 个业务逻辑服务
│   │   ├── collectors/       # 6 个数据采集器 (3财经 + 3科技)
│   │   ├── processors/       # 4 个数据处理管道
│   │   ├── scheduler/        # APScheduler 任务编排
│   │   ├── core/             # 安全/Redis/中间件/SSE路由
│   │   ├── db/               # 数据库会话 + 种子数据
│   │   └── alembic/          # 数据库迁移
│   ├── requirements/         # base.txt / dev.txt / prod.txt
│   ├── Dockerfile            # 多阶段构建
│   └── entrypoint.sh         # 容器入口
│
├── frontend/                 # Vue.js 3 前端
│   ├── src/
│   │   ├── views/            # 5 个页面视图
│   │   ├── components/       # 30 个 UI 组件
│   │   │   ├── layout/       # AppLayout, Header, Sidebar
│   │   │   ├── finance/      # 9 个财经组件
│   │   │   ├── tech/         # 6 个科技组件
│   │   │   ├── dashboard/    # 6 个监控组件
│   │   │   ├── settings/     # 3 个设置组件
│   │   │   └── common/       # 4 个通用组件
│   │   ├── stores/           # 6 个 Pinia 状态仓库
│   │   ├── composables/      # 5 个组合式函数
│   │   ├── api/              # API 客户端层
│   │   ├── types/            # TypeScript 类型定义
│   │   ├── utils/            # 工具函数
│   │   └── styles/           # 全局样式
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile            # 多阶段构建
│
├── docker/                   # Docker 编排
│   ├── docker-compose.yml    # 开发环境
│   ├── docker-compose.prod.yml  # 生产环境 (overlay)
│   ├── docker-compose.test.yml  # 测试环境
│   ├── nginx/                # Nginx 配置 + SSL
│   ├── postgres/             # 数据库初始化脚本
│   └── redis/                # Redis 配置
│
├── docs/design/              # 11 份设计文档 (中文)
│   ├── architecture.md       # 总体架构
│   ├── api.md                # REST API 规范
│   ├── database.md           # 数据库设计
│   └── ...
│
├── scripts/                  # 运维脚本
│   ├── setup-dev.sh          # 一键初始化开发环境
│   ├── run-tests.sh          # 运行测试
│   ├── seed-data.sh          # 填充种子数据
│   ├── generate-migration.sh # 生成数据库迁移
│   └── clean-dev.sh          # 清理开发环境
│
└── .github/workflows/        # GitHub Actions CI/CD
    ├── ci.yml
    ├── cd-staging.yml
    └── cd-production.yml
```

---

## 功能详解

### 财经模块 (Finance)

提供类似 Google Finance 的面板式体验，通过子导航在 5 个功能面板间切换。

**子面板导航**：

| 面板 | 功能 |
|------|------|
| **Overview** | 混合视图 — 自选摘要 + 重点行情 + 头条新闻 |
| **Watchlist** | 完整自选列表 — 可拖拽排序，实时行情刷新 |
| **Search** | 搜索 — 支持代码/名称/中文名搜索，支持股票/基金/指数/商品 |
| **Indices** | 市场指数 — 13+ 全球指数网格卡片展示 |
| **Commodities** | 大宗商品 — 7+ 期货品种，按贵金属/能源/工业/农产品分组 |

**右侧固定面板**（大屏幕始终可见）：
- 📌 WatchlistMini — 自选列表迷你版（3-5 项摘要）
- 💹 NAVCalculator — 基金实时估值计算器

**基金 NAV 估值算法**：

```
NAV_estimate = NAV_official × (1 + 跟踪指数涨跌幅 × 跟踪比率)
```

> 仅适用于指数型 ETF（如沪深 300 ETF），主动管理基金仅显示官方 NAV。

**覆盖的市场指数**（13+）：

| 市场 | 指数 |
|------|------|
| 🇺🇸 美国 | S&P 500, Dow Jones, NASDAQ |
| 🇨🇳 中国 | 上证综合, 深证成份, 沪深 300 |
| 🇭🇰 香港 | 恒生指数 |
| 🇯🇵 日本 | 日经 225 |
| 🇬🇧🇩🇪🇫🇷 欧洲 | FTSE 100, DAX 40, CAC 40 |
| 🇰🇷🇮🇳 其他 | KOSPI, BSE Sensex |

**覆盖的大宗商品**（7+）：

| 类别 | 品种 |
|------|------|
| 贵金属 | 黄金, 白银 |
| 能源 | WTI 原油, 天然气 |
| 工业金属 | 铜 |
| 农产品 | 大豆, 玉米 |

**数据刷新频率**：

| 数据类型 | 交易时段 | 休市时段 |
|---------|---------|---------|
| 自选行情 | 30 秒 | 5 分钟 |
| 市场指数 | 30 秒 | 5 分钟 |
| 大宗商品 | 60 秒 | 5 分钟 |
| 基金 NAV 估值 | 120 秒 | 不刷新 |

---

### 科技模块 (Tech)

**话题驱动的资讯聚合面板**，覆盖四大前沿领域：

| 领域 | 🤖 机器人 | 🧠 AI | ⚡ 嵌入式 | 🚀 太空 |
|------|----------|-------|---------|--------|
| 子分类数 | 6 | 6 | 6 | 6 |
| 主题色 | 紫色 | 蓝色 | 橙色 | 绿色 |
| 关注话题 | 人形/工业/协作/自动驾驶/无人机/ROS | LLM/生成式AI/AI芯片/伦理/多模态/Agent | IoT/RISC-V/RTOS/FPGA/芯片/嵌入式AI | 商业航天/卫星/深空/空间站/火箭/太空制造 |

**两种视图模式**：

1. **领域面板视图**（默认）— 2×2 网格展示四大领域，每个面板 3-5 条新闻
2. **合并时间线视图** — 跨领域混合流，无限滚动加载

**话题过滤**：顶部 TopicFilter 标签栏支持按领域/子分类/热门话题多级过滤。

**排序策略**（3 种可切换）：
- 🔥 **热度优先** — 时间衰减热度算法（半衰期 12 小时）
- 🕐 **时间优先** — 最新发布优先
- 🎯 **相关性** — 基于用户兴趣标签加权

**数据源（20+）示例**：

| 领域 | 数据源 |
|------|--------|
| AI | MIT Tech Review, HackerNews, ArXiv CS.AI, OpenAI Blog |
| 机器人 | IEEE RSS, The Robot Report, ROS Blog, HackerNews |
| 嵌入式 | Embedded.com, RISC-V Blog, Hackaday, EE Times |
| 太空 | SpaceNews, NASA News, SpaceX Updates, ESA News |
| 跨领域 | Reddit, Google News |

---

### 监控仪表盘 (Dashboard)

> 仅 Admin 角色用户可访问。

**五类指标监控**：

| 类别 | 指标示例 | 采集频率 |
|------|---------|---------|
| 🖥️ 系统状态 | API 状态、SSE 连接数、QPS、响应时间、错误率 | 10-30 秒 |
| 📡 数据源健康 | 各数据源状态/成功率/延迟/最新采集时间 | 5 分钟 |
| 🗄️ 数据库 | PG 连接池/库大小、Redis 内存/连接数/Key数 | 30 秒 |
| 💻 资源消耗 | CPU/内存使用率、磁盘 IO/容量、网络流量 | 10-30 秒 |
| 📊 业务指标 | 活跃用户数、今日数据条目、分类分布 | 5 分钟 |

**资源优化策略**：
- psutil 非阻塞采集 (< 1ms/次)
- SSE 增量推送（仅推送变化值）
- Chart.js 懒加载 + 非活跃自动暂停
- 每小时归档 1 条到 PostgreSQL（每天仅 24 行）

---

### SSO 认证

支持 5 种主流单点登录方式，**默认仅启用 Google 和 GitHub**，其他提供商可通过环境变量按需启用。

| 提供商 | 协议 | 默认启用 | 配置要求 |
|--------|------|:--------:|---------|
| **Google** | OAuth 2.0 | ✅ | Google Cloud Console 创建 OAuth Client |
| **GitHub** | OAuth 2.0 | ✅ | GitHub Settings → Developer → OAuth App |
| **Azure AD** | OpenID Connect | ❌ | Azure Portal 注册应用 |
| **Apple** | Sign-In with Apple | ❌ | Apple Developer 创建 Service ID + ES256 密钥 |
| **Facebook** | OAuth 2.0 | ❌ | Facebook App Dashboard 创建应用 |

> 所有提供商的代码实现均已保留，通过 `ENABLED_SSO_PROVIDERS` 环境变量控制启用/禁用，这是**配置驱动**而非代码删除。

**SSO 配置示例**：

```bash
# .env 文件
# 默认只启用 Google 和 GitHub
ENABLED_SSO_PROVIDERS=google,github

# 启用 Azure AD
ENABLED_SSO_PROVIDERS=google,github,azure_ad

# 启用所有提供商
ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook
```

**查询已启用的提供商**（前端可用此接口动态显示登录按钮）：

```bash
curl http://localhost:8000/api/v1/auth/sso/providers
# {"enabled_providers": ["google", "github"]}
```

**认证架构**：
- JWT 双 Token 方案：Access Token (1h) + Refresh Token (7d)
- Access Token：存 localStorage，Bearer 方式传递
- Refresh Token：存 HttpOnly Cookie，单次使用轮换
- Redis Token 黑名单（登出时失效旧 Token）

**角色权限**：

| 角色 | 权限 |
|------|------|
| `admin` | 管理租户内用户/分类/数据源 + 所有功能 |
| `member` | 使用功能 + 管理个人自选列表 |
| `viewer` | 仅查看 |

---

### 实时推送 (SSE)

**SSE (Server-Sent Events)** 是 InstantBoard 的实时核心，优于 WebSocket 的理由：
- 原生浏览器支持 (EventSource API)
- 自动重连机制
- 纯 HTTP，无需特殊协议升级
- HTTP/2 多路复用

**8 种事件类型**：

| 事件 | 频道 | 频率 |
|------|------|------|
| `quote_update` | finance | 30 秒 |
| `market_index_update` | finance | 30 秒 |
| `commodity_update` | finance | 60 秒 |
| `nav_estimate_update` | finance | 120 秒 |
| `item_update` | tech | 2-5 分钟 |
| `topic_stats_update` | tech | 15 分钟 |
| `source_health_update` | finance/tech | 实时 |
| `system_metric_update` | dashboard | 10 秒 |

> 所有频道均有 30 秒心跳保活。通过 Redis Pub/Sub 实现多进程间的消息分发。

---

## API 文档

启动后端后自动生成交互式 API 文档：

- **Swagger UI**: http://localhost:8000/api/v1/docs
- **ReDoc**: http://localhost:8000/api/v1/redoc

**主要端点一览**：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/health` | 健康检查 |
| `GET` | `/api/v1/auth/sso/providers` | 获取已启用的 SSO 提供商 *(无需认证)* |
| `POST` | `/api/v1/auth/sso/{provider}` | SSO 登录 |
| `POST` | `/api/v1/auth/refresh` | 刷新 Token |
| `GET` | `/api/v1/auth/me` | 当前用户信息 |
| `GET` | `/api/v1/finance/search?q=` | 搜索证券 |
| `GET` | `/api/v1/finance/quote/{symbol}` | 获取行情 |
| `GET` | `/api/v1/finance/market-indices` | 市场指数 |
| `GET` | `/api/v1/finance/commodities` | 大宗商品 |
| `GET` | `/api/v1/finance/fund/{symbol}/nav` | 基金 NAV 估值 |
| `GET/POST/DELETE` | `/api/v1/finance/watchlist` | 自选列表 CRUD |
| `GET` | `/api/v1/tech/news` | 科技新闻列表 |
| `GET` | `/api/v1/tech/topics` | 话题标签 |
| `GET` | `/api/v1/categories` | 分类列表 |
| `GET` | `/api/v1/sources` | 数据源列表 |
| `GET` | `/api/v1/dashboard/system` | 系统状态 |
| `GET` | `/api/v1/dashboard/services` | 服务状态 |
| `GET` | `/api/v1/dashboard/data-sources` | 数据源健康 |
| `GET` | `/api/v1/stream/{category}` | SSE 实时流 |
| `GET` | `/api/v1/stream/stats` | SSE 连接统计 |

---

## 常用命令

**Makefile 提供 30+ 快捷命令**：

```bash
make help              # 显示所有可用命令
```

### 开发环境

| 命令 | 说明 |
|------|------|
| `make dev` | 启动开发环境 |
| `make up` | 启动所有服务 |
| `make down` | 停止所有服务 |
| `make logs` | 查看所有日志 |
| `make logs-api` | 查看 API 日志 |
| `make backend-shell` | 进入后端容器 |
| `make frontend-shell` | 进入前端容器 |
| `make db-shell` | 进入 PostgreSQL |
| `make redis-shell` | 进入 Redis CLI |

### 代码质量

| 命令 | 说明 |
|------|------|
| `make lint` | 运行 lint 检查 (ruff + eslint) |
| `make lint-fix` | 自动修复 lint 问题 |
| `make format` | 格式化代码 (ruff + prettier) |

### 数据库

| 命令 | 说明 |
|------|------|
| `make migrate` | 运行数据库迁移 |
| `make makemigration msg="描述"` | 生成迁移文件 |
| `make seed` | 填充种子数据 |
| `make reset-db` | 重置数据库 |

### 构建

| 命令 | 说明 |
|------|------|
| `make build` | 构建所有开发镜像 |
| `make build-prod` | 构建所有生产镜像 |

### 清理

| 命令 | 说明 |
|------|------|
| `make clean` | 清理容器+镜像+数据 |
| `make clean-data` | 仅清理数据 volumes |

---

## 测试

```bash
# 运行所有测试
make test

# 仅后端测试
make test-backend

# 按类型运行
make test-unit          # 单元测试
make test-integration   # 集成测试
make test-e2e           # 端到端测试

# Docker 隔离测试环境
make test-docker
```

> 测试配置使用 SQLite + MockRedis，无需外部依赖。pytest 配置在 `backend/pyproject.toml`。

---

## 生产部署

### Docker Compose 生产部署

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

**生产环境服务清单**：

| 服务 | 说明 | 资源限制 |
|------|------|---------|
| `nginx` | 反向代理 + SSL + 限流 | 0.5 CPU / 256M |
| `api` | FastAPI + Gunicorn | 1.0 CPU / 512M |
| `worker` | 后台任务处理 | 0.5 CPU / 256M |
| `postgres` | PostgreSQL 15 | 1.0 CPU / 768M |
| `redis` | Redis 7 (密码保护) | 0.5 CPU / 256M |
| `frontend` | 静态资源 (build 产物) | 0.25 CPU / 128M |

### 开发 vs 生产差异

| 项目 | 开发 | 生产 |
|------|------|------|
| API | 端口 8000 直接访问 | Nginx 代理 (80/443) |
| 数据库 | 端口 5432 可直连 | 不对外暴露 |
| 前端 | Vite dev server (HMR) | Nginx 托管构建产物 |
| Worker | 集成在 API (APScheduler) | 独立 Worker 进程 |
| HTTPS | 无 | TLS 1.3 + HSTS |
| 重启策略 | 无 | always |
| 密码 | 固定开发密码 | .env 强密码 |

---

## CI/CD

使用 GitHub Actions 实现自动化：

| 工作流 | 触发条件 | 步骤 |
|--------|---------|------|
| **CI** (`ci.yml`) | Push/PR 到任何分支 | Lint → TypeCheck → Test → Build |
| **CD Staging** (`cd-staging.yml`) | Push 到 `staging` | 构建镜像 → 推送 GHCR → SSH 部署 → 冒烟测试 |
| **CD Production** (`cd-production.yml`) | Release 发布 | 构建镜像 → 推送 GHCR → 滚动部署 → 健康检查 → 失败回滚 |

**分支策略**：
- `main` — 生产分支
- `staging` — 预发布分支
- `feature/*` — 功能分支

---

## 数据源

### 财经数据源

| 数据源 | 类型 | 覆盖 | 优先级 |
|--------|------|------|--------|
| yfinance | Python 库 | 全球股票/指数/期货/基金 | 🔵 首选 |
| Alpha Vantage | REST API | 美股/外汇/技术指标 | 🟢 备用 |
| 东方财富 | 网页抓取 | A 股/港股/中国基金 | 🟢 补充 |

### 科技数据源（按领域）

| 领域 | 主要数据源 |
|------|-----------|
| AI | MIT Tech Review, HackerNews, ArXiv, OpenAI Blog, The Batch |
| 机器人 | IEEE RSS, The Robot Report, ROS Blog, HackerNews |
| 嵌入式 | Embedded.com, RISC-V Blog, Hackaday, EE Times |
| 太空 | SpaceNews, NASA, SpaceX, ESA, Ars Technica |
| 通用 | Reddit, Google News |

### 数据采集器

| 采集器 | 数据源类型 |
|--------|-----------|
| YFinanceCollector | Yahoo Finance (股票/指数/商品) |
| AlphaVantageCollector | Alpha Vantage API |
| EastMoneyCollector | 东方财富 (A 股/基金) |
| RSSCollector | 通用 RSS 源 |
| HackerNewsCollector | HackerNews API/RSS |
| ArxivCollector | ArXiv 论文 |

### 数据管道

```mermaid
graph LR
    A["📡 数据采集器<br/>7 个 Collector"] -->|"原始数据"| B["🔍 去重处理器<br/>DedupProcessor<br/>Redis Set + MD5"]
    B -->|"唯一数据"| C["🚫 内容过滤器<br/>FilterProcessor<br/>黑名单 + 质量阈值"]
    C -->|"合格数据"| D["🏷️ 自动分类器<br/>CategorizerProcessor<br/>200+ 关键词映射<br/>TechTopicExtractor"]
    D -->|"分类 + 标签"| E["🔄 格式转换器<br/>TransformerProcessor<br/>HTML 清理 · UTC 标准化"]
    E -->|"标准化数据"| F[("🐘 PostgreSQL<br/>items / finance_quotes")]
    E -->|"缓存写入"| G[("🔴 Redis<br/>行情 · 指数 · 商品")]
    E -->|"实时推送"| H["📡 SSE EventRouter<br/>Pub/Sub 分发"]

    style A fill:#e3f2fd,stroke:#1565c0,color:#000
    style B fill:#fff3e0,stroke:#ef6c00,color:#000
    style C fill:#fce4ec,stroke:#c62828,color:#000
    style D fill:#e8f5e9,stroke:#2e7d32,color:#000
    style E fill:#f3e5f5,stroke:#7b1fa2,color:#000
    style F fill:#e8eaf6,stroke:#283593,color:#000
    style G fill:#ffebee,stroke:#b71c1c,color:#000
    style H fill:#e0f7fa,stroke:#00838f,color:#000
```

---

## 多租户

InstantBoard 支持多租户架构：

- **数据隔离**：PostgreSQL 行级安全 (RLS)，每个查询自动过滤 `tenant_id`
- **Redis 隔离**：Key 前缀 `t:{tenant_id}:*`
- **租户计划**：free / pro / enterprise
- **租户限制**：max_users, max_categories, max_sources

启动后自动创建默认租户（`default`），所有数据归属该租户。

---

## 常见问题

### Q: MongoDB 必须安装吗？
**不需要**。MongoDB 仅作为可选组件用于存储原始抓取数据（初始版本不启用）。核心数据全部由 PostgreSQL 管理。如需启用：
```bash
cd docker && docker compose --profile mongodb up -d
```

### Q: 如何只使用部分 SSO？
通过 `.env` 中的 `ENABLED_SSO_PROVIDERS` 环境变量控制启用哪些提供商（默认 `google,github`）。前端可通过 `GET /api/v1/auth/sso/providers` 接口动态获取已启用的提供商列表，只显示对应的登录按钮。未启用的提供商无需配置凭据。

### Q: yfinance 被限流了怎么办？
系统会自动故障转移到 Alpha Vantage。建议配置 `ALPHA_VANTAGE_API_KEY` 作为备用。

### Q: 如何更换涨跌颜色？
默认中国配色（🔴红涨🟢绿跌）。可在 Settings → ProfileSettings 中切换为国际配色（🟢绿涨🔴红跌）。

### Q: SSE 连接断开怎么办？
SSE (EventSource) 原生支持自动重连。后端 30 秒心跳保活，断开后浏览器会自动重新连接。

### Q: 如何添加自定义分类/数据源？
1. 前端：Settings 页面 → 新增 Category → 配置关键词和刷新间隔
2. 后端 API：`POST /api/v1/categories` → `POST /api/v1/sources`

---

## 开发指南

### 添加新的数据采集器

1. 在 `backend/app/collectors/` 创建新文件
2. 继承 `BaseCollector`，实现 `fetch()` 方法
3. 在 `collectors/__init__.py` 的 `COLLECTOR_REGISTRY` 中注册

### 添加新的 API 端点

1. 在 `backend/app/api/v1/` 创建新路由文件
2. 定义 Pydantic schema 在 `schemas/`
3. 实现业务逻辑在 `services/`
4. 在 `api/router.py` 中注册子路由

### 添加新的前端组件

1. 在 `frontend/src/components/` 对应目录创建 `.vue` 文件
2. 如需要状态管理，在 `stores/` 中添加 Pinia store
3. 如需 API 调用，在 `api/` 中添加请求函数

### 设计文档

详细设计文档位于 `docs/design/`：

| 文档 | 内容 |
|------|------|
| `architecture.md` | 总体系统架构 |
| `api.md` | 完整 REST API + SSE 规范 |
| `database.md` | PostgreSQL/Redis/MongoDB 设计 |
| `data-flow.md` | 数据管道 + SSE 推送流程 |
| `frontend.md` | Vue.js 前端架构 |
| `finance-tab.md` | 财经模块详细设计 |
| `tech-tab.md` | 科技模块详细设计 |
| `dashboard-tab.md` | Dashboard 监控设计 |
| `content-categories.md` | 内容分类体系 |
| `data-sources.md` | 外部数据源目录 |
| `security.md` | 安全架构设计 |
| `infrastructure.md` | DevOps + Docker + CI/CD |

---

## 许可证

本项目基于 [GNU General Public License v3.0](LICENSE) 开源。
