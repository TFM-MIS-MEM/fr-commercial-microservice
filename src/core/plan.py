"""El plan de la cuenta, según SPACE (F10, TFM-MIS-MEM/docs#42 y #51).

SPACE evalúa la tarifa publicada en SPHERE (`faceapi`). Este módulo es lo único
del servicio que sabe que existe: una llamada para leer el plan y otra para
imputar unidades. Los controladores reciben un `Plan` y llaman a `consume`.

CÓMO SE LEE EL PLAN: el token de pricing (`POST /features/{cuenta}/pricing-token`)
es la única operación de SPACE que devuelve en una llamada todas las
funcionalidades y los límites efectivos, complementos incluidos. Se lee su
carga útil SIN verificar la firma: llega de SPACE por la red interna, y
verificarla obligaría a repartir su secreto. Se usa `pricingContext`, nunca la
evaluación por funcionalidad que trae el token: `identityManagement` comprueba
unidades, y con las unidades agotadas su evaluación es `false`; decidir con ella
dejaría a una cuenta sin unidades sin poder listar ni borrar identidades. Tampoco
`subscriptionContext`, que SPACE cachea y no refresca al renovar un periodo.

CÓMO SE CONSUME: una evaluación con consumo esperado de la funcionalidad, con la
misma cantidad en las tres claves de unidades (SPACE exige todas las que nombra
la expresión). SPACE compara ANTES de sumar, así que una cuenta puede pasarse del
límite en lo que cueste una operación. No se deshace nunca: el deshacer de SPACE
restaura una instantánea en vez de restar, y con dos peticiones de la misma
cuenta en vuelo borraría el consumo de la otra (estudio de la sesión 22,
NOTAS §5.5). Lo que falle después de consumir queda cobrado.

EL CONTRATO FREE se crea aquí, en el primer uso: si SPACE no tiene contrato para
la cuenta, se crea con el plan FREE y se repite. Es una desviación consciente:
los contratos son de `pricing`, que hoy no se entera de las altas (no hay
`account.created`). Un `409` por carrera cuenta como éxito.

SPACE CAÍDO O MAL CONFIGURADO es `503 PLAN_UNAVAILABLE`, sin admitir por
omisión, como la pasarela con `auth` caído. Cuentan como caída: el error de red,
el tiempo agotado (250 ms al conectar, 1 s en total por llamada, sin
reintentos), cualquier 5xx, una respuesta que no se entiende y un `error` no
nulo en la evaluación, que es una tarifa que SPACE no puede evaluar. Responder
eso con un `403` diría al cliente que su plan no lo incluye. SPACE no devuelve
un 503 rápido cuando le falta su Redis o su MongoDB: se queda colgado (NOTAS
§5.7), de ahí el plazo propio.
"""

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx2
from fastapi import Depends, Request

from src.core.errors import ErrorReportError
from src.core.identity import Identity, require_identity
from src.core.logger import get_logger

logger = get_logger(__name__)

#: El servicio en SPACE: el nombre de la tarifa en SPHERE, en minúsculas. Es el
#: prefijo de toda funcionalidad y todo límite.
SERVICE = "faceapi"

#: Los tres límites de unidades, en el orden en que se informa el agotado.
UNIT_LIMITS = {
    "capacityUnitsPerMinute": "minute",
    "capacityUnitsPerHour": "hour",
    "capacityUnitsPerMonth": "month",
}

#: Funcionalidad de cada modelo.
MODEL_FEATURES = {"xs": "modelXs", "s": "modelS", "base": "modelBase"}

#: Las funcionalidades que el servicio consulta. Si la tarifa cargada no trae
#: alguna, no se puede decidir: `503`, no un `403` que diría que el plan no la
#: incluye.
FEATURES = frozenset(
    {
        "faceDetection",
        "pairVerification",
        "galleryManagement",
        "identityManagement",
        "identityVerification",
        "embeddingExtraction",
        "gallerySearch",
        "configurableThreshold",
        "apiChannel",
        *MODEL_FEATURES.values(),
    }
)
STATE_LIMITS = frozenset({"maxFaces", "maxGalleries"})

CONNECT_TIMEOUT = 0.25
TOTAL_TIMEOUT = 1.0
RETRY_AFTER_SECONDS = 5

#: Versión del contrato Free que se crea. SPACE la sustituye por la versión
#: fijada de la tarifa (`all_pick`, scripts/space-bootstrap.sh): con un servicio
#: sincronizado desde SPHERE, el servidor impone la versión del objetivo
#: (`enforceSpherePolicy`), envíe el cliente la que envíe.
CONTRACT_VERSION = "fijada-por-space"

#: Margen para comparar consumos: SPACE suma dobles (1,55 + … da
#: 27.150000000000002).
EPSILON = 1e-9


# ------------------------------------------------------------------ errores


def unavailable() -> ErrorReportError:
    return ErrorReportError(
        503,
        "PLAN_UNAVAILABLE",
        "The plan of the account cannot be checked right now",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def feature_not_in_plan(feature: str) -> ErrorReportError:
    return ErrorReportError(
        403, "FEATURE_NOT_IN_PLAN", "The plan does not include this feature", {"feature": feature}
    )


def model_not_in_plan(model: str) -> ErrorReportError:
    return ErrorReportError(
        403, "MODEL_NOT_IN_PLAN", "The plan does not include this model", {"model": model}
    )


def quota_exceeded(limit: str, reset_at: str) -> ErrorReportError:
    return ErrorReportError(
        403,
        "QUOTA_EXCEEDED",
        "The capacity units of the plan are exhausted",
        {"limit": limit, "resetAt": reset_at},
    )


# ------------------------------------------------------------------ el plan


@dataclass(frozen=True)
class Plan:
    """Funcionalidades y límites efectivos de la cuenta, sin el prefijo del servicio.

    Lleva la cuenta y el cliente con que se leyó, para que un controlador impute
    unidades con `plan.consume(...)` sin recibir aparte la identidad y SPACE.
    """

    features: Mapping[str, bool]
    limits: Mapping[str, float]
    account_id: str = ""
    space: "Space | None" = field(default=None, repr=False, compare=False)

    def has(self, feature: str) -> bool:
        return self.features[feature] is True

    def require(self, feature: str) -> None:
        if not self.has(feature):
            raise feature_not_in_plan(feature)

    def allows_model(self, model: str) -> bool:
        return self.has(MODEL_FEATURES[model])

    def require_model(self, model: str) -> None:
        if not self.allows_model(model):
            raise model_not_in_plan(model)

    async def consume(self, feature: str, units: float) -> None:
        """Imputa las unidades de la operación, o `403 QUOTA_EXCEEDED`."""
        await self.space.consume(self.account_id, feature, units)

    def require_threshold(self, threshold: float | None) -> None:
        """Enviar `threshold` exige el umbral configurable; sin él, nada que comprobar."""
        if threshold is not None:
            self.require("configurableThreshold")

    @classmethod
    def from_pricing_context(
        cls, context: Mapping[str, Any], account_id: str = "", space: "Space | None" = None
    ) -> "Plan":
        prefix = f"{SERVICE}-"

        def strip(values: Mapping[str, Any]) -> dict[str, Any]:
            return {k[len(prefix) :]: v for k, v in values.items() if k.startswith(prefix)}

        features = strip(context["features"])
        limits = strip(context["usageLimits"])
        missing = (FEATURES - features.keys()) | (
            (STATE_LIMITS | UNIT_LIMITS.keys()) - limits.keys()
        )
        if missing:
            raise ValueError(f"la tarifa cargada no trae {sorted(missing)}")
        return cls(
            features=features,
            limits={k: float(v) for k, v in limits.items()},
            account_id=account_id,
            space=space,
        )


def token_payload(token: str) -> dict[str, Any]:
    """La carga útil de un JWT, sin verificar la firma (véase la cabecera)."""
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


# ------------------------------------------------------------------ el cliente


class Space:
    """Cliente de SPACE. Uno por proceso, creado en el `lifespan`."""

    def __init__(
        self, base_url: str, api_key: str, transport: httpx2.AsyncBaseTransport | None = None
    ):
        self._client = httpx2.AsyncClient(
            base_url=f"{base_url}/api/v1",
            headers={"x-api-key": api_key},
            timeout=httpx2.Timeout(TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warm_up(self) -> None:
        """Abre la primera conexión al arrancar, con un plazo amplio.

        La primera conexión de un proceso es la cara (resolver el nombre,
        cargar el transporte), y con 250 ms de plazo una vez acabó en `503` en
        la primera petición tras recrear el contenedor. Así la paga el arranque.
        Si SPACE no responde, no se impide arrancar: cada petición lo dirá.
        """
        try:
            await self._client.get("/healthcheck", timeout=5.0)
        except httpx2.HTTPError as exc:
            logger.warning("space_warm_up_failed", error=type(exc).__name__)

    async def _call(self, method: str, path: str, body: Any = None) -> httpx2.Response:
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT):
                response = await self._client.request(method, path, json=body)
        except (httpx2.HTTPError, TimeoutError) as exc:
            logger.warning("space_unreachable", path=path, error=type(exc).__name__)
            raise unavailable() from None
        if response.status_code >= 500:
            logger.warning("space_error", path=path, status=response.status_code)
            raise unavailable()
        return response

    @staticmethod
    def _json(response: httpx2.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError:
            logger.warning("space_unreadable", path=path, status=response.status_code)
            raise unavailable() from None

    async def plan(self, account_id: str) -> Plan:
        path = f"/features/{quote(account_id, safe='')}/pricing-token"
        response = await self._call("POST", path, {})
        if response.status_code == 404:
            await self._create_free_contract(account_id)
            response = await self._call("POST", path, {})
        if response.status_code != 200:
            logger.warning("space_unexpected", path=path, status=response.status_code)
            raise unavailable()
        try:
            payload = token_payload(self._json(response, path)["pricingToken"])
            return Plan.from_pricing_context(payload["pricingContext"], account_id, self)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("space_bad_plan", error=str(exc))
            raise unavailable() from None

    async def _create_free_contract(self, account_id: str) -> None:
        response = await self._call(
            "POST",
            "/contracts",
            {
                "userContact": {"userId": account_id, "username": account_id},
                "billingPeriod": {"autoRenew": True, "renewalDays": 30},
                "contractedServices": {SERVICE: CONTRACT_VERSION},
                "subscriptionPlans": {SERVICE: "FREE"},
                "subscriptionAddOns": {},
            },
        )
        if response.status_code not in (201, 409):
            logger.warning("space_contract_not_created", status=response.status_code)
            raise unavailable()
        logger.info("space_free_contract", created=response.status_code == 201)

    async def consume(self, account_id: str, feature: str, units: float) -> None:
        """Imputa `units` a los tres límites de unidades, o `403 QUOTA_EXCEEDED`."""
        path = f"/features/{quote(account_id, safe='')}/{SERVICE}-{feature}"
        body = {f"{SERVICE}-{limit}": units for limit in UNIT_LIMITS}
        response = await self._call("POST", path, body)
        result = self._json(response, path) if response.status_code == 200 else None
        if not isinstance(result, dict) or result.get("error") is not None:
            logger.warning("space_cannot_consume", status=response.status_code, result=result)
            raise unavailable()
        if result.get("eval") is True:
            return

        exhausted = self._exhausted(result, units)
        if exhausted is None:
            # `eval: false` con las unidades disponibles solo puede ser la
            # funcionalidad, que ya se comprobó con el plan: una carrera con un
            # cambio de plan. Se responde como lo que es.
            raise feature_not_in_plan(feature)
        raise quota_exceeded(UNIT_LIMITS[exhausted], await self._reset_at(account_id, exhausted))

    @staticmethod
    def _exhausted(result: Mapping[str, Any], units: float) -> str | None:
        """El primer límite agotado: el consumido ANTES de esta petición ya llega
        al límite. SPACE devuelve en `used` lo consumido más lo pedido."""
        used, limit = result.get("used") or {}, result.get("limit") or {}
        for name in UNIT_LIMITS:
            key = f"{SERVICE}-{name}"
            if key in used and key in limit and used[key] - units >= limit[key] - EPSILON:
                return name
        return None

    async def _reset_at(self, account_id: str, limit: str) -> str:
        path = f"/contracts/{quote(account_id, safe='')}"
        response = await self._call("GET", path)
        try:
            return self._json(response, path)["usageLevels"][SERVICE][limit]["resetTimeStamp"]
        except (KeyError, TypeError):
            logger.warning("space_no_reset", status=response.status_code)
            raise unavailable() from None


# ------------------------------------------------------------------ dependencias


def get_space(request: Request) -> Space:
    return request.app.state.space


async def require_plan(
    identity: Identity = Depends(require_identity), space: Space = Depends(get_space)
) -> Plan:
    """El plan de la cuenta, con el canal ya comprobado.

    Se monta en los routers: una ruta que se añada nace consultando el plan,
    como nace exigiendo la identidad. Por el canal de API, el plan tiene que
    incluirlo; por el web, todos lo incluyen (`webChannel`).
    """
    plan = await space.plan(identity.account_id)
    if identity.channel == "api":
        plan.require("apiChannel")
    return plan


def requires(feature: str):
    """Dependencia: el plan incluye `feature`, o `403 FEATURE_NOT_IN_PLAN`.

    Para las funcionalidades que valen para todo un router (la gestión de
    galerías, la de identidades) o para una ruta entera (la búsqueda).
    """

    async def dependency(plan: Plan = Depends(require_plan)) -> None:
        plan.require(feature)

    return dependency


# ------------------------------------------------------------------ unidades


def search_units(face_count: int) -> float:
    """1,5 + 0,05 por cada 1.000 rostros de la galería, proporcional."""
    return 1.5 + 0.05 * face_count / 1000


#: Unidades de las operaciones de coste fijo, medidas en la campaña (#62).
UNITS = {
    "faceDetection": 1.0,
    "pairVerification": 3.0,
    "embeddingExtraction": 1.5,
    "identityManagement": 1.5,
    "identityVerification": 1.5,
}
