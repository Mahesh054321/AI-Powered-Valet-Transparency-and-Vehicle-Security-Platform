# ==============================================================================
#  Convenience targets. On Windows without `make`, the equivalent commands are
#  documented in README.md and in the root package.json (`npm run <script>`).
# ==============================================================================

.PHONY: help setup setup-backend setup-frontend db-init db-reset db-check \
        dev api web test coverage lint lint-backend lint-frontend build verify clean

PYTHON ?= python
BACKEND := backend
FRONTEND := frontend

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: setup-backend setup-frontend ## Install backend and frontend dependencies

setup-backend: ## Install Python dependencies
	cd $(BACKEND) && $(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

setup-frontend: ## Install JavaScript dependencies
	cd $(FRONTEND) && npm install

db-init: ## Create extensions, apply migrations, seed reference data
	cd $(BACKEND) && $(PYTHON) utils/scripts/init_db.py --seed

db-reset: ## Drop all tables, then rebuild and reseed
	cd $(BACKEND) && $(PYTHON) utils/scripts/init_db.py --reset --seed

db-check: ## Verify the schema without changing it
	cd $(BACKEND) && $(PYTHON) utils/scripts/init_db.py --check-only

verify: ## Run the pre-flight environment check
	cd $(BACKEND) && $(PYTHON) utils/scripts/verify_setup.py

api: ## Run the Flask API on :5000
	cd $(BACKEND) && $(PYTHON) utils/scripts/run_api.py

web: ## Run the React dev server on :5173
	cd $(FRONTEND) && npm run dev

dev: ## Run API and web together
	npm run dev

test: ## Run the backend test suite
	cd $(BACKEND) && $(PYTHON) -m pytest

coverage: ## Run tests with a coverage report
	cd $(BACKEND) && $(PYTHON) -m pytest --cov=app --cov=config --cov=routes --cov=utils --cov=database --cov=services --cov-report=term-missing

lint: lint-backend lint-frontend ## Lint backend and frontend

lint-backend: ## Ruff over the Python code
	cd $(BACKEND) && $(PYTHON) -m ruff check .

lint-frontend: ## ESLint over the React code
	cd $(FRONTEND) && npx eslint .

build: ## Production build of the frontend
	cd $(FRONTEND) && npm run build

clean: ## Remove caches and build output
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(BACKEND)/.coverage $(FRONTEND)/dist
