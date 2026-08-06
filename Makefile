.PHONY: help dev up down logs logs-api logs-worker \
       test test-unit test-integration test-e2e test-backend test-frontend \
       lint lint-fix format \
       build build-api build-frontend \
       migrate makemigration seed \
       clean clean-data reset-db \
       prod-up prod-down \
       backend-shell frontend-shell \
       gen-admin-hash \
       local-dev local-dev-backend local-dev-frontend

# ========================================
# InstantBoard Development Commands
# ========================================

COMPOSE_DIR := docker
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
COMPOSE := cd $(COMPOSE_DIR) && $(DOCKER_COMPOSE)
COMPOSE_PROD := cd $(COMPOSE_DIR) && $(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml

# Default target
help:           ## Show all available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ========================================
# Development environment
# ========================================

dev:            ## Start the development environment (docker compose up)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(COMPOSE) up -d --build

up:             ## Start all services
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(COMPOSE) up -d

down:           ## Stop all services
	$(COMPOSE) down

logs:           ## Tail logs of all services
	$(COMPOSE) logs -f

logs-api:       ## Tail API logs
	$(COMPOSE) logs -f api

logs-worker:    ## Tail worker logs
	$(COMPOSE) logs -f worker

logs-frontend:  ## Tail frontend logs
	$(COMPOSE) logs -f frontend

logs-db:        ## Tail database logs
	$(COMPOSE) logs -f postgres

# ========================================
# Container shells
# ========================================

backend-shell:  ## Open a shell in the backend container
	$(COMPOSE) exec api bash

frontend-shell: ## Open a shell in the frontend container
	$(COMPOSE) exec frontend bash

db-shell:       ## Open the PostgreSQL shell
	$(COMPOSE) exec postgres psql -U instantboard -d instantboard_dev

redis-shell:    ## Open the Redis CLI
	$(COMPOSE) exec redis redis-cli

# ========================================
# Testing
# ========================================

test:           ## Run all tests
	$(MAKE) test-backend
	$(MAKE) test-frontend

test-unit:      ## Run unit tests
	cd backend && pytest -m unit -v

test-integration: ## Run integration tests
	cd backend && pytest -m integration -v

test-e2e:       ## Run end-to-end tests
	cd backend && pytest -m e2e -v

test-backend:   ## Run backend tests
	$(COMPOSE) exec api pytest --cov=app --cov-report=term-missing -v || \
	cd backend && pytest --cov=app --cov-report=term-missing -v

test-frontend:  ## Run frontend tests
	cd frontend && npm run test || \
	$(COMPOSE) exec frontend npm run test

test-docker:    ## Run the Docker test environment
	$(COMPOSE) -f docker-compose.test.yml up --abort-on-container-exit

# ========================================
# Code quality
# ========================================

lint:           ## Run code checks (ruff + eslint)
	cd backend && ruff check app/ tests/ && ruff format --check app/
	cd frontend && npx eslint src/ && npx prettier --check src/

lint-fix:       ## Auto-fix lint issues
	cd backend && ruff check --fix app/ tests/ && ruff format app/
	cd frontend && npx eslint --fix src/ && npx prettier --write src/

format:         ## Format code (ruff format + prettier)
	cd backend && ruff format app/
	cd frontend && npx prettier --write src/

# ========================================
# Building
# ========================================

build:          ## Build all Docker images
	$(COMPOSE) build

build-api:      ## Build the API image
	$(COMPOSE) build api

build-frontend: ## Build the frontend image
	$(COMPOSE) build frontend

build-prod:     ## Build the production images
	$(COMPOSE_PROD) build

# ========================================
# Database
# ========================================

migrate:        ## Run database migrations
	$(COMPOSE) exec api alembic upgrade head || \
	cd backend && alembic upgrade head

makemigration:  ## Generate a migration file (requires msg="description")
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(msg)" || \
	cd backend && alembic revision --autogenerate -m "$(msg)"

seed:           ## Run seed data initialization
	$(COMPOSE) exec api python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())" || \
	cd backend && python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"

# Generate a bcrypt hash for ADMIN_PASSWORD_HASH. Pass the secret via PASS=...,
# otherwise the script prompts securely on stdin. Example:
#   make gen-admin-hash PASS='MySecret'
gen-admin-hash: ## Generate a bcrypt hash for ADMIN_PASSWORD_HASH (use PASS=...)
	@if [ -x backend/.venv/bin/python ]; then \
		cd backend && .venv/bin/python ../scripts/gen_admin_password_hash.py $(PASS); \
	else \
		cd backend && python ../scripts/gen_admin_password_hash.py $(PASS); \
	fi

# ========================================
# Cleanup
# ========================================

clean:          ## Clean up Docker resources (containers + images + volumes)
	$(COMPOSE) down -v --rmi local

clean-data:     ## Clean up data volumes only
	$(COMPOSE) down -v

reset-db:       ## Reset the database (remove volume + recreate)
	$(COMPOSE) down -v
	$(COMPOSE) up -d postgres redis
	@echo "Waiting for PostgreSQL to be ready..."
	sleep 5
	$(COMPOSE) up -d api

# ========================================
# Production
# ========================================

prod-up:        ## Start the production environment
	$(COMPOSE_PROD) up -d

prod-down:      ## Stop the production environment
	$(COMPOSE_PROD) down

prod-logs:      ## Tail production logs
	$(COMPOSE_PROD) logs -f

# ========================================
# Local development (without Docker)
# ========================================

local-dev:      ## Local development (requires Python/Node/PostgreSQL/Redis)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(MAKE) local-dev-backend & $(MAKE) local-dev-frontend & wait

local-dev-backend: ## Start the backend locally (uvicorn --reload)
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

local-dev-frontend: ## Start the frontend locally (vite dev server)
	cd frontend && npm run dev
