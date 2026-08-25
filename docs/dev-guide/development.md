# 开发指南

## 添加新的数据采集器

1. 在 `backend/app/collectors/` 创建新文件
2. 继承 `BaseCollector`，实现 `fetch_data()` / `parse_data()` 抽象方法（可按需覆写可选的 `validate_data()`）
3. 在 `collectors/__init__.py` 的 `COLLECTOR_REGISTRY` 中注册

## 添加新的 API 端点

1. 在 `backend/app/api/v1/` 创建新路由文件
2. 定义 Pydantic schema 在 `schemas/`
3. 实现业务逻辑在 `services/`
4. 在 `api/router.py` 中注册子路由

## 添加新的前端组件

1. 在 `frontend/src/components/` 对应目录创建 `.vue` 文件
2. 如需要状态管理，在 `stores/` 中添加 Pinia store
3. 如需 API 调用，在 `api/` 中添加请求函数

## 设计文档

详细设计文档位于 `docs/dev-guide/design/`（wiki 页名前缀 `dev-guide-design-`），共 14 份，完整清单见侧边栏**设计文档**分组；建议从 [总体架构](design/architecture.md) 读起。

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

> ⚠️ **未实现**：`make test-e2e` 目标虽存在，但仓库中目前没有任何端到端测试用例。
