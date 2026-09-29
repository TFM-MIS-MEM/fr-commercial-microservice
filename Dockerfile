# Base ANCLADA por resumen, no por etiqueta (TFM-MIS-MEM/docs#108).
#
# `python:3.11-slim` es una etiqueta móvil: sin resumen, la imagen se construye
# sobre la copia que tenga la caché de cada máquina, y lo que lleva dentro deja de
# ser un hecho del repositorio. Medido con Trivy el 2026-09-29, solo paquetes del
# sistema, HIGH y CRITICAL: la copia local del 31/08 daba 54 HIGH y 3 CRITICAL,
# 13 de ellos con arreglo publicado; la etiqueta recién bajada (Debian 13.7, este
# resumen) da 44 HIGH, 0 CRITICAL y ninguno con arreglo. El mismo `FROM`, dos
# resultados, según la caché.
#
# Con el resumen, la base es la misma en todas partes y actualizarla pasa a ser un
# acto deliberado: se cambia esta línea, en un commit, con su medición. Es lo que
# `requirements.txt` con resúmenes ya hacía para las dependencias de Python;
# `pip-audit` no ve el sistema operativo, y quién vigila esta base y con qué es
# #108.
#
# Lo que el resumen NO fija: la capa de `apt-get install` de más abajo resuelve
# contra los repositorios de Debian al construir.
FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl: lo usa el healthcheck de los composes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup && adduser --system --group appuser

# El árbol fijado y con resúmenes: la imagen instala EXACTAMENTE lo que audita la
# CI, y ni un paquete que no esté en requirements.txt.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY ./src ./src
# El contrato. Sin él no hay /docs ni /openapi.json, y el servicio se niega a
# arrancar (src/api/docs.py). La plantilla solo copiaba src/.
COPY ./openapi ./openapi

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "--factory", "src.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
