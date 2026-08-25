# 常见问题

### Q: MongoDB 必须安装吗？
**不需要**。MongoDB 仅作为可选组件用于存储原始抓取数据（初始版本不启用）。核心数据全部由 PostgreSQL 管理。如需启用：
```bash
cd docker && docker compose --profile mongodb up -d
# docker-compose --profile mongodb up -d  # V1 也同样适用
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

### Q: `could not determine a constructor for the tag '!reset'`
已修复。若你本地 fork 仍有此问题，升级到最新版即可。项目现已移除所有 `!reset` / `!override` YAML 标签以兼容 V1 `docker-compose`。

### Q: `no matching manifest for linux/arm/v7`
项目 CI 现默认推送多架构 manifest list。确保：
1. 触发的是最新 CI 流程（含多架构构建阶段）
2. 服务器上的 Docker pull 时会自动选择匹配架构

### Q: 部署服务器是 armv7l 但镜像总是拉不到
检查 CI 是否已合并多架构构建 commit，以及服务器架构是否正确：
```bash
docker info | grep "Architecture"
# 确认服务器架构为 armv7l
```
