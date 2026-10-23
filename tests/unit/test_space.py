"""El cliente de SPACE (src/core/plan.py), contra un transporte HTTP falso.

Las pruebas de contrato usan un SPACE doblado (tests/support/plan.py); estas
prueban lo que el doble sustituye: cómo se lee el plan del token de pricing, el
contrato Free en el primer uso, la imputación de unidades y, sobre todo, que
toda forma de «SPACE no sabe o no puede» acaba en `503 PLAN_UNAVAILABLE` y no en
un `403` que diría que el plan no lo incluye.

Las respuestas imitan las medidas contra SPACE v1.7.1 en el estudio de la
sesión 22 (NOTAS §5): la evaluación responde siempre 200, la denegación es
`eval: false`, y `used` es lo consumido MÁS lo que se pedía.
"""

import asyncio
import base64
import json
import time

import httpx2
import pytest

from src.core import plan as plan_module
from src.core.errors import ErrorReportError
from src.core.plan import SERVICE, Space

ACCOUNT = "66f0000000000000000000a1"

FEATURES = {
    "faceDetection": True,
    "pairVerification": True,
    "galleryManagement": True,
    "identityManagement": True,
    "identityVerification": True,
    "embeddingExtraction": False,
    "gallerySearch": False,
    "configurableThreshold": False,
    "apiChannel": False,
    "webChannel": True,
    "modelXs": True,
    "modelS": False,
    "modelBase": False,
}
LIMITS = {
    "maxFaces": 100,
    "maxGalleries": 1,
    "capacityUnitsPerMinute": 8,
    "capacityUnitsPerHour": 48,
    "capacityUnitsPerMonth": 4800,
}
RESET = {
    "capacityUnitsPerMinute": "2026-10-22T21:45:22.550Z",
    "capacityUnitsPerHour": "2026-10-22T22:43:22.550Z",
    "capacityUnitsPerMonth": "2026-11-22T21:43:22.550Z",
}


def _b64(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def token(features=FEATURES, limits=LIMITS) -> str:
    payload = {
        "sub": ACCOUNT,
        # La evaluación por funcionalidad del token NO se usa: aquí, a
        # propósito, todo `false`.
        "features": {f"{SERVICE}-{k}": {"eval": False} for k in features},
        "pricingContext": {
            "features": {f"{SERVICE}-{k}": v for k, v in features.items()},
            "usageLimits": {f"{SERVICE}-{k}": v for k, v in limits.items()},
        },
        "subscriptionContext": {},
    }
    return f"{_b64({'alg': 'HS256'})}.{_b64(payload)}.firma-que-no-se-verifica"


def evaluation(eval_, used=None, limit=None, error=None) -> dict:
    return {"eval": eval_, "limit": limit, "used": used, "error": error}


def units(minute, hour, month) -> dict:
    return {
        f"{SERVICE}-capacityUnitsPerMinute": minute,
        f"{SERVICE}-capacityUnitsPerHour": hour,
        f"{SERVICE}-capacityUnitsPerMonth": month,
    }


class Fake:
    """Transporte falso: cada ruta, una lista de respuestas (o excepciones) en orden."""

    def __init__(self, **routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        answer = self.routes[key].pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)


TOKEN = f"POST /api/v1/features/{ACCOUNT}/pricing-token"
CONTRACTS = "POST /api/v1/contracts"
CONTRACT = f"GET /api/v1/contracts/{ACCOUNT}"


def consume_route(feature: str) -> str:
    return f"POST /api/v1/features/{ACCOUNT}/{SERVICE}-{feature}"


def space(fake) -> Space:
    return Space("http://space.test:3000", "org_k", transport=httpx2.MockTransport(fake))


def run(coro):
    return asyncio.run(coro)


def assert_unavailable(exc_info) -> None:
    exc = exc_info.value
    assert (exc.status_code, exc.code) == (503, "PLAN_UNAVAILABLE")
    assert exc.headers == {"Retry-After": "5"}


# ------------------------------------------------------------------ el plan


def test_the_plan_is_read_from_the_pricing_context_of_the_token_without_the_prefix():
    fake = Fake(**{TOKEN: [(200, {"pricingToken": token()})]})

    plan = run(space(fake).plan(ACCOUNT))

    assert plan.features == FEATURES
    assert plan.limits == {k: float(v) for k, v in LIMITS.items()}
    assert plan.account_id == ACCOUNT
    assert fake.requests[0].headers["x-api-key"] == "org_k"


def test_without_contract_a_free_one_is_created_and_the_plan_read_again():
    fake = Fake(
        **{
            TOKEN: [
                (404, {"error": f"Contract with userId {ACCOUNT} not found"}),
                (200, {"pricingToken": token()}),
            ],
            CONTRACTS: [(201, {})],
        }
    )

    plan = run(space(fake).plan(ACCOUNT))

    assert plan.has("faceDetection")
    created = json.loads(fake.requests[1].content)
    assert created["userContact"]["userId"] == ACCOUNT
    assert created["subscriptionPlans"] == {SERVICE: "FREE"}
    assert created["billingPeriod"] == {"autoRenew": True, "renewalDays": 30}
    assert created["subscriptionAddOns"] == {}


def test_a_409_creating_the_contract_is_another_request_that_got_there_first():
    fake = Fake(
        **{
            TOKEN: [(404, {}), (200, {"pricingToken": token()})],
            CONTRACTS: [(409, {"error": "CONFLICT: Contract already exists"})],
        }
    )

    assert run(space(fake).plan(ACCOUNT)).has("faceDetection")


def test_a_contract_that_cannot_be_created_is_503():
    fake = Fake(**{TOKEN: [(404, {})], CONTRACTS: [(400, {"error": "Invalid subscription"})]})

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).plan(ACCOUNT))

    assert_unavailable(exc_info)


def test_a_tariff_without_a_feature_the_service_checks_is_503_not_403():
    features = {k: v for k, v in FEATURES.items() if k != "gallerySearch"}
    fake = Fake(**{TOKEN: [(200, {"pricingToken": token(features)})]})

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).plan(ACCOUNT))

    assert_unavailable(exc_info)


@pytest.mark.parametrize(
    "answer",
    [
        (500, {"error": "boom"}),
        (503, {"error": "Space cannot verify credentials right now."}),
        (200, {"no": "token"}),
        (200, {"pricingToken": "no-es-un-jwt"}),
        (401, {"error": "Invalid Organization API Key"}),
        (403, {"error": "role (EVALUATION) does not have permission"}),
        httpx2.ConnectError("refused"),
        httpx2.ConnectTimeout("connect"),
        httpx2.ReadTimeout("read"),
    ],
    ids=["500", "503", "sin-token", "token-roto", "401", "403", "conexion", "conectar", "leer"],
)
def test_every_way_space_cannot_answer_is_503(answer):
    fake = Fake(**{TOKEN: [answer]})

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).plan(ACCOUNT))

    assert_unavailable(exc_info)


def test_a_space_that_hangs_is_503_within_the_total_timeout(monkeypatch):
    """SPACE con su Redis o su MongoDB caído no responde nunca (NOTAS §5.7)."""
    monkeypatch.setattr(plan_module, "TOTAL_TIMEOUT", 0.2)

    async def hanging(_request):
        await asyncio.sleep(5)

    started = time.perf_counter()
    with pytest.raises(ErrorReportError) as exc_info:
        run(
            Space("http://space.test:3000", "org_k", transport=httpx2.MockTransport(hanging)).plan(
                ACCOUNT
            )
        )

    assert_unavailable(exc_info)
    assert time.perf_counter() - started < 1


# ------------------------------------------------------------------ el consumo


def test_consume_sends_the_same_amount_on_the_three_unit_limits():
    fake = Fake(
        **{consume_route("gallerySearch"): [(200, evaluation(True, units(1.55, 1.55, 1.55)))]}
    )

    run(space(fake).consume(ACCOUNT, "gallerySearch", 1.55))

    assert json.loads(fake.requests[0].content) == units(1.55, 1.55, 1.55)


@pytest.mark.parametrize(
    ("used", "limit", "expected"),
    [
        (units(10.5, 10.5, 10.5), units(8, 48, 4800), "minute"),
        (units(3, 49.55, 49.55), units(8, 48, 4800), "hour"),
        (units(3, 3, 4801.55), units(8, 48, 4800), "month"),
        # SPACE suma dobles: 25.6 + 1.55 = 27.150000000000002.
        (units(27.150000000000002, 1, 1), units(25.6, 48, 4800), "minute"),
    ],
    ids=["minuto", "hora", "mes", "coma-flotante"],
)
def test_quota_exceeded_says_which_limit_and_when_it_renews(used, limit, expected):
    route = "capacityUnitsPer" + {"minute": "Minute", "hour": "Hour", "month": "Month"}[expected]
    fake = Fake(
        **{
            consume_route("identityManagement"): [(200, evaluation(False, used, limit))],
            CONTRACT: [
                (
                    200,
                    {
                        "usageLevels": {
                            SERVICE: {route: {"resetTimeStamp": RESET[route], "consumed": 9}}
                        }
                    },
                )
            ],
        }
    )

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).consume(ACCOUNT, "identityManagement", 1.55))

    exc = exc_info.value
    assert (exc.status_code, exc.code) == (403, "QUOTA_EXCEEDED")
    assert exc.details == {"limit": expected, "resetAt": RESET[route]}


def test_a_denial_with_units_left_is_the_feature_not_the_quota():
    fake = Fake(
        **{
            consume_route("gallerySearch"): [
                (200, evaluation(False, units(1.5, 1.5, 1.5), units(8, 48, 4800)))
            ]
        }
    )

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).consume(ACCOUNT, "gallerySearch", 1.5))

    assert (exc_info.value.status_code, exc_info.value.code) == (403, "FEATURE_NOT_IN_PLAN")


@pytest.mark.parametrize(
    "answer",
    [
        (200, evaluation(False, error={"code": "PARSE_ERROR", "message": "no expression"})),
        (200, evaluation(False, error={"code": "INVALID_EXPECTED_CONSUMPTION", "message": "x"})),
        (404, {"error": "Contract not found"}),
        (500, {"error": "Value already exists in cache, please use a different key."}),
        httpx2.ReadTimeout("read"),
    ],
    ids=["parse-error", "consumo-invalido", "sin-contrato", "500-que-cobra", "plazo"],
)
def test_consumption_that_space_cannot_decide_is_503(answer):
    fake = Fake(**{consume_route("faceDetection"): [answer]})

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).consume(ACCOUNT, "faceDetection", 1))

    assert_unavailable(exc_info)


def test_search_units_are_proportional_to_the_gallery_size():
    assert plan_module.search_units(0) == 1.5
    assert plan_module.search_units(1000) == pytest.approx(1.55)
    assert plan_module.search_units(50000) == pytest.approx(4.0)
    assert plan_module.search_units(500) == pytest.approx(1.525)


def test_a_body_that_is_not_json_is_503():
    fake = Fake(**{TOKEN: [(200, "<html>nginx</html>")]})

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).plan(ACCOUNT))

    assert_unavailable(exc_info)


def test_a_denial_whose_reset_cannot_be_read_is_503_not_a_quota_without_details():
    fake = Fake(
        **{
            consume_route("faceDetection"): [
                (200, evaluation(False, units(9, 9, 9), units(8, 48, 4800)))
            ],
            CONTRACT: [(200, {"usageLevels": {}})],
        }
    )

    with pytest.raises(ErrorReportError) as exc_info:
        run(space(fake).consume(ACCOUNT, "faceDetection", 1))

    assert_unavailable(exc_info)
