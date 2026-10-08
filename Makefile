.PHONY: help install lock audit dev dev-docker prod-docker lint lint-fix format test check clean

help:
	@echo "make install     Instala producción y desarrollo desde los ficheros fijados."
	@echo "make lock        Regenera requirements.txt y requirements-dev.txt (con resúmenes)."
	@echo "make audit       Audita el árbol de PRODUCCIÓN (requirements.txt)."
	@echo "make dev         Arranca el servicio en local (exige MONGODB_URI)."
	@echo "make dev-docker  Levanta servicio + MongoDB con docker-compose.dev.yml."
	@echo "make test        Pruebas, DENTRO del contenedor de desarrollo (exige la red tfm-network)."
	@echo "make lint | lint-fix | format | check (bandit) | clean"

install:
	pip install --require-hashes -r requirements.txt -r requirements-dev.txt

# El orden importa: requirements-dev.in restringe contra requirements.txt.
lock:
	pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements.txt requirements.in
	pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements-dev.txt requirements-dev.in

# El equivalente de `pnpm audit --prod`: lo que se despliega, no el árbol de
# desarrollo.
audit:
	pip-audit -r requirements.txt --require-hashes --disable-pip --progress-spinner off

dev:
	uvicorn --factory src.main:create_app --host 127.0.0.1 --port 8000 --reload

dev-docker:
	docker compose -f docker-compose.dev.yml up --build

prod-docker:
	docker compose -f docker-compose.yml up -d --build

lint:
	ruff check src tests

lint-fix:
	ruff check --fix src tests

format:
	ruff format src tests

# En el contenedor, no en el anfitrión: `torch==2.14.1+cpu` solo existe para
# linux, y la CI lo instala igual en x86_64. `run` levanta el MongoDB del compose
# y espera a que esté sano; las pruebas usan un servidor, sin base (conftest).
#
# `env -u LOG_LEVEL`: el compose fija `LOG_LEVEL=debug`, y la prueba del valor
# por omisión depende de que la variable no exista (TFM-MIS-MEM/docs#110).
test:
	docker compose -f docker-compose.dev.yml run --rm \
		-e TEST_MONGODB_URI=mongodb://fr-commercial-mongodb:27017 \
		fr-commercial env -u LOG_LEVEL pytest

check:
	bandit -c pyproject.toml -r src

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache/ .ruff_cache/ coverage.xml .coverage
