.PHONY: help install lint lint-fix format test test-cov dev dev-docker prod-docker check clean pre-commit

help:
	@echo "Comandos disponibles:"
	@echo "  make install      - Instala las dependencias y pre-commit hooks."
	@echo "  make dev          - Ejecuta el servidor en modo desarrollo (hot-reload)."
	@echo "  make dev-docker   - Levanta el entorno en Docker con docker-compose.dev.yml (hot-reload)."
	@echo "  make prod-docker  - Construye y levanta la imagen para producción."
	@echo "  make lint         - Analiza el código con Ruff."
	@echo "  make lint-fix     - Arregla errores automáticamente con Ruff."
	@echo "  make format       - Formatea el código con Ruff."
	@echo "  make test         - Ejecuta los tests unitarios e integración (Pytest)."
	@echo "  make test-cov     - Ejecuta los tests y muestra la cobertura."
	@echo "  make check        - Ejecuta auditoría de seguridad (Bandit)."
	@echo "  make clean        - Limpia archivos cacheados e innecesarios."
	@echo "  make pre-commit   - Ejecuta todos los pre-commit hooks de manera manual."

install:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt
	pip install pre-commit
	pre-commit install

dev:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

dev-docker:
	docker-compose -f docker-compose.dev.yml up --build

prod-docker:
	docker-compose -f docker-compose.yml up -d --build

lint:
	ruff check src tests

lint-fix:
	ruff check --fix src tests

format:
	ruff format src tests

test:
	pytest

test-cov:
	pytest --cov=src --cov-report=term-missing

check:
	bandit -c pyproject.toml -r src

pre-commit:
	pre-commit run --all-files

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	rm -f coverage.xml
