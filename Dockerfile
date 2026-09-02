# Base image: Python 3.11 slim
FROM python:3.11-slim as base

# Establecer variables de entorno
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema requeridas para construir paquetes o librerías pesadas
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Crear un usuario no-root
RUN addgroup --system appgroup && adduser --system --group appuser

# Copiar dependencias
COPY requirements.txt .

# Instalar dependencias
# Nota: Si se usa PyTorch en un futuro usar: RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install -r requirements.txt

# Copiar el código de la aplicación
COPY ./src ./src

# Cambiar permisos al usuario no-root
RUN chown -R appuser:appgroup /app

# Cambiar al usuario no-root
USER appuser

# Exponer el puerto
EXPOSE 8000

# Comando para ejecutar la aplicación
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
