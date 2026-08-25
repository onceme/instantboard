# API 速览

启动后端后自动生成交互式 API 文档：

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

**主要端点一览**：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/health` | 健康检查 |
| `GET` | `/api/v1/health/detail` | 详细健康检查（PostgreSQL/Redis 连通性） |
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
| `PUT` | `/api/v1/finance/watchlist/reorder` | 自选列表排序 |
| `GET` | `/api/v1/tech/news` | 科技新闻列表 |
| `GET` | `/api/v1/tech/topics` | 话题标签 |
| `GET` | `/api/v1/categories` | 分类列表 |
| `GET` | `/api/v1/sources` | 数据源列表 |
| `GET` | `/api/v1/dashboard/system` | 系统状态 |
| `GET` | `/api/v1/dashboard/services` | 服务状态 |
| `GET` | `/api/v1/dashboard/data-sources` | 数据源健康 |
| `GET` | `/api/v1/stream/{category}` | SSE 实时流 |
| `GET` | `/api/v1/stream/stats` | SSE 连接统计 |

运行时以 Swagger 为权威；完整请求/响应契约见 [API 设计](../dev-guide/design/api.md)。
