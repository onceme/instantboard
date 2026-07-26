.PHONY: help dev up down logs logs-api logs-worker \
       test test-unit test-integration test-e2e test-backend test-frontend \
       lint lint-fix format \
       build build-api build-frontend \
       migrate makemigration seed \
       clean clean-data reset-db \
       prod-up prod-down \
       backend-shell frontend-shell \
       local-dev local-dev-backend local-dev-frontend

# ========================================
# InstantBoard Development Commands
# ========================================

COMPOSE_DIR := docker
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
COMPOSE := cd $(COMPOSE_DIR) && $(DOCKER_COMPOSE)
COMPOSE_PROD := cd $(COMPOSE_DIR) && $(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml

# Default target
help:           ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ========================================
# 开发环境
# ========================================

dev:            ## 启动开发环境 (docker compose up)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(COMPOSE) up -d --build

up:             ## 启动所有服务
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(COMPOSE) up -d

down:           ## 停止所有服务
	$(COMPOSE) down

logs:           ## 查看所有服务日志
	$(COMPOSE) logs -f

logs-api:       ## 查看API日志
	$(COMPOSE) logs -f api

logs-worker:    ## 查看Worker日志
	$(COMPOSE) logs -f worker

logs-frontend:  ## 查看前端日志
	$(COMPOSE) logs -f frontend

logs-db:        ## 查看数据库日志
	$(COMPOSE) logs -f postgres

# ========================================
# 容器 Shell
# ========================================

backend-shell:  ## 进入后端容器 shell
	$(COMPOSE) exec api bash

frontend-shell: ## 进入前端容器 shell
	$(COMPOSE) exec frontend bash

db-shell:       ## 进入PostgreSQL shell
	$(COMPOSE) exec postgres psql -U instantboard -d instantboard_dev

redis-shell:    ## 进入Redis CLI
	$(COMPOSE) exec redis redis-cli

# ========================================
# 测试
# ========================================

test:           ## 运行所有测试
	$(MAKE) test-backend
	$(MAKE) test-frontend

test-unit:      ## 运行单元测试
	cd backend && pytest -m unit -v

test-integration: ## 运行集成测试
	cd backend && pytest -m integration -v

test-e2e:       ## 运行端到端测试
	cd backend && pytest -m e2e -v

test-backend:   ## 运行后端测试
	$(COMPOSE) exec api pytest --cov=app --cov-report=term-missing -v || \
	cd backend && pytest --cov=app --cov-report=term-missing -v

test-frontend:  ## 运行前端测试
	cd frontend && npm run test || \
	$(COMPOSE) exec frontend npm run test

test-docker:    ## 运行Docker测试环境
	$(COMPOSE) -f docker-compose.test.yml up --abort-on-container-exit

# ========================================
# 代码质量
# ========================================

lint:           ## 运行代码检查 (ruff + eslint)
	cd backend && ruff check app/ && ruff format --check app/
	cd frontend && npx eslint src/ && npx prettier --check src/

lint-fix:       ## 自动修复lint问题
	cd backend && ruff check --fix app/ && ruff format app/
	cd frontend && npx eslint --fix src/ && npx prettier --write src/

format:         ## 格式化代码 (ruff format + prettier)
	cd backend && ruff format app/
	cd frontend && npx prettier --write src/

# ========================================
# 构建
# ========================================

build:          ## 构建所有Docker镜像
	$(COMPOSE) build

build-api:      ## 构建API镜像
	$(COMPOSE) build api

build-frontend: ## 构建前端镜像
	$(COMPOSE) build frontend

build-prod:     ## 构建生产镜像
	$(COMPOSE_PROD) build

# ========================================
# 数据库
# ========================================

migrate:        ## 运行数据库迁移
	$(COMPOSE) exec api alembic upgrade head || \
	cd backend && alembic upgrade head

makemigration:  ## 生成迁移文件 (需要 msg="描述")
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(msg)" || \
	cd backend && alembic revision --autogenerate -m "$(msg)"

seed:           ## 运行种子数据初始化
	$(COMPOSE) exec api python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())" || \
	cd backend && python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"

# ========================================
# 清理
# ========================================

clean:          ## 清理Docker资源 (容器+镜像+volumes)
	$(COMPOSE) down -v --rmi local

clean-data:     ## 仅清理数据volumes
	$(COMPOSE) down -v

reset-db:       ## 重置数据库 (删除volume+重建)
	$(COMPOSE) down -v
	$(COMPOSE) up -d postgres redis
	@echo "Waiting for PostgreSQL to be ready..."
	sleep 5
	$(COMPOSE) up -d api

# ========================================
# 生产环境
# ========================================

prod-up:        ## 启动生产环境
	$(COMPOSE_PROD) up -d

prod-down:      ## 停止生产环境
	$(COMPOSE_PROD) down

prod-logs:      ## 查看生产环境日志
	$(COMPOSE_PROD) logs -f

# ========================================
# 本地开发 (不使用Docker)
# ========================================

local-dev:      ## 本地开发 (需要Python/Node/PostgreSQL/Redis)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(MAKE) local-dev-backend & $(MAKE) local-dev-frontend & wait

local-dev-backend: ## 本地启动后端 (uvicorn --reload)
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

local-dev-frontend: ## 本地启动前端 (vite dev server)
	cd frontend && npm run dev
