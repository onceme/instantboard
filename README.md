# InstantBoard — 实时信息聚合消息板

> 基于 FastAPI + Vue 3 + SSE 的实时信息聚合平台，汇集全球金融市场数据与前沿科技资讯。

[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.4-42b883.svg)](https://vuejs.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docker.com)

---

## 项目简介

InstantBoard 是一个**实时信息聚合消息板**服务，采用前后端分离架构，通过 SSE (Server-Sent Events) 实时推送数据到浏览器。

**两大核心内容板块**：

| 板块 | 内容 | 数据来源 |
|------|------|---------|
| 📈 **财经 (Finance)** | 股票/基金搜索、自选关注列表、基金 NAV 实时估值、全球市场指数、大宗商品期货 | Yahoo Finance、Alpha Vantage、东方财富 |
| 🔬 **科技 (Tech)** | 机器人、AI、大规模嵌入式、太空科技四大领域资讯聚合 | RSS 20+ 源、HackerNews、ArXiv、SpaceNews |

## 核心功能

- 🔄 **SSE 实时推送** — 服务端主动推送行情和新闻更新，浏览器零延迟刷新
- 📊 **自选关注列表** — 自定义股票/基金关注列表，实时涨跌幅追踪
- 💰 **基金 NAV 估值** — 基于跟踪指数的 ETF 实时净值估算（指数跟踪法）
- 🌍 **全球市场指数** — S&P 500、纳斯达克、上证、恒生、日经等 13+ 指数
- 🛢️ **大宗商品** — 黄金、原油、白银、天然气等 7+ 期货品种
- 🏷️ **话题标签过滤** — 三级标签体系（领域→子分类→话题），支持跨领域筛选
- 📡 **20+ 数据源** — RSS/API/网页抓取三种采集方式，自动故障转移
- 🔐 **5 种 SSO 登录** — 默认启用 Google / GitHub，可按需启用 Azure AD / Apple / Facebook
- 🏢 **多租户架构** — 应用层 `tenant_id` 过滤隔离，租户独立配置
- 🛡️ **多层安全** — JWT 双 Token + Refresh 轮换黑名单 + CORS
- 📱 **响应式设计** — 手写 CSS 变量 + 媒体查询适配桌面/平板/手机
- 🌓 **深/浅色主题** — 运行时切换，支持涨跌颜色配置
- 📈 **轻量监控仪表盘** — 系统/CPU/内存、数据源健康、SSE 连接统计

## 技术要点

- **前端**：Vue 3.4 + TypeScript + Vite 8，Pinia 状态管理，手写 CSS
- **后端**：Python 3.11+ / FastAPI，Async 全链路，SQLAlchemy 2.0 ORM，APScheduler
- **实时**：SSE + Redis Pub/Sub，30s 心跳，自动重连
- **存储**：PostgreSQL 17（开发）/ 15（生产）+ Redis 7，应用层 `tenant_id` 隔离

详见 [架构与代码导读](docs/dev-guide/architecture-overview.md)。

## 快速开始

```bash
git clone https://github.com/your-org/instantboard.git && cd instantboard
bash scripts/setup-dev.sh
# 启动完成后访问 http://localhost:3000
```

> 不使用 Docker 时需本地预装 Python 3.11+ / Node 24+ / PostgreSQL / Redis；`make help` 查看全部 30+ 快捷命令。

完整说明见 [快速开始](docs/user-guide/getting-started.md) 与 [配置说明](docs/user-guide/configuration.md)。

## 文档索引

详细文档位于 [docs/](docs/Home.md)（同步为 GitHub Wiki）。

### 用户手册

| 页面 | 内容 |
|------|------|
| [快速开始](docs/user-guide/getting-started.md) | 环境要求 / Docker 一键启动 / 本地运行 |
| [配置说明](docs/user-guide/configuration.md) | 全部环境变量分组与 .env 示例 |
| [生产部署](docs/user-guide/deployment.md) | 部署流程 / SSL 与 HTTPS / 多架构 / 生产 .env 模板 |
| [财经模块](docs/user-guide/features-finance.md) | 子面板 / 基金 NAV 估值 / 指数与商品清单 |
| [科技模块](docs/user-guide/features-tech.md) | 四领域聚合 / 视图模式 / 排序策略 |
| [监控仪表盘](docs/user-guide/features-dashboard.md) | 系统 / 数据源健康 / 资源指标 |
| [认证与登录](docs/user-guide/authentication.md) | SSO 5 家 + 本地管理员 /ibadmin + 角色权限 |
| [实时推送 SSE](docs/user-guide/real-time-sse.md) | 7 种业务事件 / 心跳 / 自动重连 |
| [API 速览](docs/user-guide/api-reference.md) | 端点总表 + Swagger / ReDoc 入口 |
| [常用命令](docs/user-guide/commands.md) | Makefile 30+ 快捷命令 |
| [数据源清单](docs/user-guide/data-sources.md) | 财经 / 科技数据源 + 采集器 + 数据管道 |
| [多租户](docs/user-guide/multi-tenant.md) | 租户隔离与限制 |
| [常见问题](docs/user-guide/faq.md) | FAQ |

### 开发者手册

| 页面 | 内容 |
|------|------|
| [架构与代码导读](docs/dev-guide/architecture-overview.md) | 技术栈全表 + 架构图 + 项目结构 |
| [开发指南](docs/dev-guide/development.md) | 新采集器 / 新端点 / 新组件 + 测试 |
| [CI/CD](docs/dev-guide/cicd.md) | GitHub Actions 工作流与分支策略 |
| [设计文档](docs/dev-guide/design/) | 16 份设计文档（总体架构 / API / 数据库 / 数据流 / 安全等） |

## 许可证

本项目基于 [GNU General Public License v3.0](LICENSE) 开源。
