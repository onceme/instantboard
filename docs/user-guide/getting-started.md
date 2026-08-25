# 快速开始

## 环境要求

| 工具 | 最低版本 | 说明 |
|------|---------|------|
| Docker Engine | 24+ | 容器运行时 |
| Docker Compose | V2+ | 服务编排 |
| Git | 2.0+ | 代码拉取 |

> 💡 如果不使用 Docker，需要：Python 3.11+、Node.js 24+（vite 8 / eslint 9 要求 Node 20+，CI 与镜像均使用 24）、PostgreSQL 15+（开发环境 compose 使用 17）、Redis 7

## 一键启动（Docker）

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

> 💡 安装脚本和 Makefile 自动检测 `docker compose`（V2 插件）或 `docker-compose`（V1 独立版），两者均可使用，无需手动选择。

**启动完成后访问**：

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/docs |
| API 文档 (ReDoc) | http://localhost:8000/redoc |

**Docker Compose 服务清单**（开发环境）：

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| `postgres` | postgres:17 | 5432 | 主数据库 |
| `redis` | redis:7-alpine | 6379 | 缓存 + Pub/Sub |
| `api` | 自定义 Python 3.11-slim | 8000 | FastAPI (热重载) |
| `frontend` | 自定义 Node 24-alpine | 3000 | Vite dev server (HMR) |

> MongoDB 6 默认不启动，可通过 `--profile mongodb` 按需启用。

> 💡 上表的宿主机端口发布与热重载挂载全部来自 `docker/docker-compose.override.yml`（开发专用覆盖层，`docker compose up` 自动加载）：基础 `docker-compose.yml` 刻意不发布任何宿主机端口，由 override 发布 5432/6379/27017/8000/3000/3001 并挂载前后端源码目录实现热重载。显式使用 `-f` 合并生产文件时不会加载 override，因此生产不会暴露这些端口。

## 本地开发（无 Docker）

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

环境变量配置见[配置说明](configuration.md)。
