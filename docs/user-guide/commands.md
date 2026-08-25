# 常用命令

**Makefile 提供 30+ 快捷命令**：

```bash
make help              # 显示所有可用命令
```

## 开发环境

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

## 代码质量

| 命令 | 说明 |
|------|------|
| `make lint` | 运行 lint 检查 (ruff + eslint) |
| `make lint-fix` | 自动修复 lint 问题 |
| `make format` | 格式化代码 (ruff + prettier) |

## 数据库

| 命令 | 说明 |
|------|------|
| `make migrate` | 运行数据库迁移 |
| `make makemigration msg="描述"` | 生成迁移文件 |
| `make seed` | 填充种子数据 |
| `make reset-db` | 重置数据库 |

## 构建

| 命令 | 说明 |
|------|------|
| `make build` | 构建所有开发镜像 |
| `make build-prod` | 构建所有生产镜像 |

## 清理

| 命令 | 说明 |
|------|------|
| `make clean` | 清理容器+镜像+数据 |
| `make clean-data` | 仅清理数据 volumes |
