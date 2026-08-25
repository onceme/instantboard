# InstantBoard

> 基于 FastAPI + Vue 3 + SSE 的实时信息聚合平台，汇集全球金融市场数据与前沿科技资讯。

InstantBoard 是一个**实时信息聚合消息板**服务，采用前后端分离架构，通过 SSE (Server-Sent Events) 实时推送数据到浏览器：📈 **财经板块**覆盖股票/基金搜索、自选列表、基金 NAV 估值、全球指数与大宗商品；🔬 **科技板块**聚合机器人、AI、嵌入式、太空四大领域资讯。

本 Wiki 与仓库 `docs/` 目录一一对应，分为**用户手册**（使用者与部署者）与**开发者手册**（贡献者）两大板块。

## 用户手册

| 页面 | 内容 |
|------|------|
| [快速开始](user-guide/getting-started.md) | 环境要求 / Docker 一键启动 / 本地无 Docker 运行 |
| [配置说明](user-guide/configuration.md) | 全部环境变量分组与 .env 示例 |
| [生产部署](user-guide/deployment.md) | 部署流程 / SSL 与 HTTPS / 多架构 / 生产 .env 模板 |
| [财经模块](user-guide/features-finance.md) | 子面板 / 基金 NAV 估值 / 指数与商品清单 |
| [科技模块](user-guide/features-tech.md) | 四领域聚合 / 视图模式 / 排序策略 |
| [监控仪表盘](user-guide/features-dashboard.md) | 系统 / 数据源健康 / 资源指标 |
| [认证与登录](user-guide/authentication.md) | SSO 5 家 + 本地管理员 /ibadmin + 角色权限 |
| [实时推送 SSE](user-guide/real-time-sse.md) | 7 种业务事件 / 心跳 / 自动重连 |
| [API 速览](user-guide/api-reference.md) | 端点总表 + Swagger / ReDoc 入口 |
| [常用命令](user-guide/commands.md) | Makefile 30+ 快捷命令 |
| [数据源清单](user-guide/data-sources.md) | 财经 / 科技数据源 + 采集器 + 数据管道 |
| [多租户](user-guide/multi-tenant.md) | 租户隔离与限制 |
| [常见问题](user-guide/faq.md) | FAQ |

## 开发者手册

| 页面 | 内容 |
|------|------|
| [架构与代码导读](dev-guide/architecture-overview.md) | 技术栈全表 + 架构图 + 项目结构 |
| [开发指南](dev-guide/development.md) | 新采集器 / 新端点 / 新组件 + 测试 |
| [CI/CD](dev-guide/cicd.md) | GitHub Actions 工作流与分支策略 |

设计文档共 14 份，位于 `dev-guide/design/`（wiki 页名前缀 `dev-guide-design-`），完整清单见侧边栏**设计文档**分组；建议从[总体架构](dev-guide/design/architecture.md)读起。
