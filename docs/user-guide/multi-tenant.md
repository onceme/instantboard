# 多租户

InstantBoard 支持多租户架构：

- **数据隔离**：应用层 `tenant_id` 过滤（每个查询由代码附加租户条件）
- **Redis 隔离**：Key 前缀 `t:{tenant_id}:*`
- **租户计划**：free / pro / enterprise
- **租户限制**：max_users, max_categories, max_sources

> ⚠️ **未实现**：PostgreSQL 行级安全 (RLS)（无 `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY`），租户隔离完全依赖应用层过滤，绕过应用直连数据库时不存在隔离。

启动后自动创建默认租户（`default`），所有数据归属该租户。
