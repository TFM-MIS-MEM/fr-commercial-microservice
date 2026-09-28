FROM python:3.11-slim AS base

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
