# CI/CD 与分支策略

使用 GitHub Actions 实现自动化：

| 工作流 | 触发条件 | 步骤 |
|--------|---------|------|
| **CI** (`ci.yml`) | Push 到 `main`/`staging`/`develop`，PR 到 `main`/`staging`（Node 24 + PostgreSQL 15） | Lint → TypeCheck → Test → Build |
| **CD Staging** (`cd-staging.yml`) | Push 到 `staging` | 构建镜像 → 推送 GHCR → SSH 部署 → 冒烟测试 |
| **CD Production** (`cd-production.yml`) | Release 发布 | 构建镜像 → 推送 GHCR → 滚动部署 → 健康检查 → 失败回滚 |
| **Wiki 同步** (`sync-wiki.yml`) | Push 到 `main` 且 `docs/**` 变更，或手动触发 | 运行 `scripts/sync-wiki.sh`，将 `docs/` 转换并推送到 `REPO.wiki.git` |

**分支策略**：
- `main` — 生产分支
- `staging` — 预发布分支
- `feature/*` — 功能分支

## Wiki 同步说明

`docs/` 是 GitHub Wiki 的单一事实来源，由 `sync-wiki.yml` 在合入 `main` 后自动同步（压平页名、剥离 frontmatter、重写站内链接）。同步依赖 `WIKI_SYNC_TOKEN` secret（具备 wiki 写权限的 PAT 或部署密钥）。

完整的 Docker 编排、部署流程与 CI/CD 细则见 [基础设施设计](design/infrastructure.md)。
