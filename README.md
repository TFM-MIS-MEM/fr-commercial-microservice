# Python FastAPI Microservice Template

Boilerplate profesional y escalable para los microservicios de Python del Trabajo de Fin de Máster (TFM) del SaaS de Reconocimiento Facial (ej. Inferencia, Modelos IA, Interfaz Triton).

Esta base estandariza la estructura adoptando un enfoque **Clean Architecture**, reforzando la seguridad, el rendimiento asíncrono y alineándose a las directrices de despliegue mediante contenedores.

---

## 🌟 Arquitectura y Funcionalidades Integradas

El código adopta las más estrictas convenciones de diseño para servicios en Python:

### 1. Clean Architecture y Estructura por Capas
Separación estricta de responsabilidades en toda la fuente:
- **`src/api/` (Rutas)**: Exposición de endpoints usando el decorador de rutas FastAPI. Actúa como capa de presentación e inyecta dependencias.
- **`src/services/` (Lógica de Negocio)**: Implementa los casos de uso, transformaciones computacionales e interacciones puras, desconectadas del orquestador HTTP (FastAPI).
- **`src/models/` (Data Holders & DTOs)**: Define modelos transaccionales de Request/Response valiándose de **Pydantic v2**, y opcionalmente mapeos relacionales si hay Base de Datos.
- **`src/core/` (Configuración y Core)**: Gestiona centralizadamente los settings (`settings.py`), middlewares, loggers (`logger.py`), y limitadores (`rate_limit.py`).

### 2. Error Report Pattern (Manejador Global de Errores)
Replicando la estructura de Node.js, todas las respuestas de error devuelven un objeto JSON consistente `{ status, message, details }`:
- **`ErrorReportException` (`src/core/exceptions.py`)**: Excepción base que debe lanzarse ante cualquier flujo erróneo controlado (equivalente a `AppError`).
- Configurado a través de **`setup_exception_handlers()`** inyectado en la instancia de la aplicación `main.py`, que previene stack traces filtrados en producción y estandariza las excepciones propias y las de Pydantic (Validación).

### 3. Validación y Rendimiento (FastAPI + Pydantic + Motor)
- Integración nativa de validaciones tipo `Body`, `Query` y `Path` de FastAPI impulsadas por Pydantic. Si fallan, el gestor global intercepta el error convirtiéndolo a Bad Request de forma natural.
- El servidor soporta concurrencia asíncrona total impulsada por asgi (`Uvicorn`) y maneja una conexión de ciclo de vida (`lifespan`) segura a MongoDB con **AsyncIOMotorClient (`motor`)**.

### 4. Seguridad Activa (Security First)
- **`CORSMiddleware`**: Gestionado centralizadamente en `src/main.py`.
- **Rate Limiting (`slowapi`)**: Limita peticiones basándose en la IP directamente en endpoints o globalmente para frenar abusos.
- **Auditorías (Bandit)**: Se incluye un linter enfocado a seguridad (`bandit`) que audita inyecciones de código, contraseñas en duro o vulnerabilidades (`make check`).

### 5. Documentación Dinámica Automática
- Al usar FastAPI, OpenAPI / Swagger se genera automáticamente derivándose de los tipos de Python (`type hints`) y Pydantic.
- Accesible interactivamente desde `/api/v1/docs` o el formato de máquina en `/api/v1/openapi.json`.

### 6. Code Quality y Linter Universal (Ruff)
- Python cuenta habitualmente con fragmentos aislados (Black, Isort, Flake8). Esta plantilla los reemplaza por completo usando **Ruff**, permitiendo formateo y linting escrito en Rust ultrarrápido configurado en **`pyproject.toml`**.

### 7. Git Hooks Preventivos (`pre-commit`)
Similar a Husky, detiene commits corruptos localmente:
- Configurado por `.pre-commit-config.yaml`, lanza chequeos nativos de Git sobre los archivos modificados que no dejarán subir nada no formateado (vía Ruff) ni tests fallidos localmente.

### 8. Integración y Despliegue Continuo (CI/CD)
Estandarizado en GitHub Actions (`.github/workflows/`):
- **`ci-cd.yml`**: Testea mediante **Pytest**, formatea y pasa Bandit en todas las Pull Requests y push directos.
- **`release.yml`**: Analiza convencionales del commit (Semantic Release) y publica versiones formales automáticamente. También ensambla la imagen Docker optimizada publicándola en tu registry preferido.

---

## 🚀 Cómo Ejecutar el Proyecto

El entorno orquesta sus abstracciones locales, chequeos e instalaciones a través del mandato utilitario **`make`** expuesto en el fichero `Makefile`. Oculta las variables en scripts de copiado.

### Prerrequisitos
- Python 3.11+
- [Opcional pero Recomendado] Docker y Docker-compose
- Proveedor de variables creado (Copia el deseado y renómbralo a `.env`).

```bash
# Copia la semilla de variables locales para arrancar.
cp .env.example .env
```

### Opción 1: Desarrollo Local (Entorno Virtual + Hot-reload)
Útil si ya cuentas con un daemon de base de datos corriendo localmente u otras dependencias resueltas.

```bash
# 1. Crea y activa tu Virtual Env
python -m venv venv
source venv/bin/activate  # Mac / Linux
# venv\Scripts\activate   # Windows

# 2. Instala librerías y configura pre-commit hooks de git
make install

# 3. Arranca el motor Uvicorn
make dev
```

### Opción 2: Entorno con Docker (Recomendado)
Sincroniza el código iterativo de tu host de trabajo con la máquina dockerizada proveyendo el entorno idéntico a producción, pero manteniendo la carga en caliente (*hot-reload*).

```bash
# 1. Configura tus credenciales y copias
cp .env.docker-compose.example .env

# 2. Levanta contenedor (Usa Dockerfile.dev subyacente para el auto reload de uvicorn)
make dev-docker
```

### Opción 3: Entorno Producción (Docker Compose)
Compila usando el `Dockerfile` de producción puro.

```bash
cp .env.docker.example .env
make prod-docker
```

---

## ✅ Comandos del `Makefile`

Se proporciona una caja de herramientas estandarizada:
- `make install`: Prepara el entorno local y dependencias desde `requirements.txt`.
- `make dev`: Lanza la APP en recarga de modo desarrollo.
- `make test`: Pasa el suite de pruebas (usando Pytest en local).
- `make test-cov`: Testea reportando estadísticas completas de cobertura.
- `make lint` / `make format`: Ordena, limpia e indenta la aplicación con `Ruff`.
- `make lint-fix`: Fuerza la auto-reparación de advertencias en `Ruff`.
- `make check`: Búsqueda de código malicioso local con `Bandit`.
- `make clean`: Limpia todos los residuales de `.pytest_cache`, `__pycache__` o basurillas.

---

## 🏗️ Estructura Completa del Directorio

```text
python-template/
├── .github/                # Workflows Automáticos (CI-CD y Releases Action)
├── src/                    # Fuente unificada
│   ├── api/                # Enrutamientos (Endpoints) presentacionales
│   ├── core/               # Orquestadores Globales (settings, logger, middlewares, rate_limiter)
│   ├── models/             # Esquemas de Datos (Validaciones Pydantic o Motor Base)
│   ├── services/           # Lógica pura / Transacciones abstractas
│   ├── utils/              # Dependencias de inyección compartidas, Helpers utilitarios.
│   └── main.py             # Entrada Uvicorn y ciclo de conexión a BBDD (Motor Async).
├── tests/                  # Suite principal de Testeo (Unitario, e2e, Mocks)
├── root configurations     # Archivos dot (.env.example, .gitignore, .dockerignore)
├── Dockerfile              # Dockerfile stage de compilación producción pura
├── Dockerfile.dev          # Docker para permitir volumen mapeado
├── Makefile                # Gestor universal de scripts rápidos
├── pyproject.toml          # Ajustes preestablecidos de herramientas Pytest, Ruff y Bandit 
└── requirements.txt        # Librerías esenciales (+ requirements-dev.txt)
```
