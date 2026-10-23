"""SPACE doblado, y los tres planes de la tarifa tal como los ve el servicio.

Las pruebas no alcanzan SPACE: `conftest.client` sustituye `get_space` por un
`FakeSpace` (`dependency_overrides`). El doble devuelve un `Plan` como el que
construye `Space.plan` desde el token de pricing, e imputa unidades como
`Space.consume`: con el límite agotado responde `403 QUOTA_EXCEEDED` sin
imputar, igual que SPACE, que no suma cuando deniega.

Lo que el doble NO prueba ---la lectura del token, el contrato Free en el primer
uso, los 503--- lo prueba `tests/unit/test_space.py` contra el cliente de verdad
con un transporte HTTP falso.

POR OMISIÓN, BUSINESS: todo incluido y límites que ninguna prueba alcanza. Así
las pruebas anteriores a F10 siguen probando lo que probaban.
"""

from dataclasses import dataclass, field

from src.core.plan import UNIT_LIMITS, Plan, get_space, quota_exceeded, unavailable

#: `resetAt` del `QUOTA_EXCEEDED` doblado.
RESET_AT = "2026-10-23T10:01:00.000Z"

_ALWAYS = {
    "faceDetection": True,
    "pairVerification": True,
    "galleryManagement": True,
    "identityManagement": True,
    "identityVerification": True,
    "modelXs": True,
}


def free() -> tuple[dict, dict]:
    features = {
        **_ALWAYS,
        "apiChannel": False,
        "embeddingExtraction": False,
        "gallerySearch": False,
        "configurableThreshold": False,
        "modelS": False,
        "modelBase": False,
    }
    limits = {
        "maxFaces": 100,
        "maxGalleries": 1,
        "capacityUnitsPerMinute": 8,
        "capacityUnitsPerHour": 48,
        "capacityUnitsPerMonth": 4800,
    }
    return features, limits


def pro() -> tuple[dict, dict]:
    features = {
        **free()[0],
        "apiChannel": True,
        "embeddingExtraction": True,
        "gallerySearch": True,
        "modelS": True,
    }
    limits = {
        "maxFaces": 5000,
        "maxGalleries": 5,
        "capacityUnitsPerMinute": 41,
        "capacityUnitsPerHour": 250,
        "capacityUnitsPerMonth": 25000,
    }
    return features, limits


def business() -> tuple[dict, dict]:
    features = {**pro()[0], "modelBase": True, "configurableThreshold": True}
    limits = {
        "maxFaces": 50000,
        "maxGalleries": 100000000,
        "capacityUnitsPerMinute": 416,
        "capacityUnitsPerHour": 2500,
        "capacityUnitsPerMonth": 250000,
    }
    return features, limits


@dataclass
class FakeSpace:
    features: dict = field(default_factory=lambda: business()[0])
    limits: dict = field(default_factory=lambda: business()[1])
    #: Límite de unidades agotado (`capacityUnitsPerMinute`, …), o None.
    exhausted: str | None = None
    #: SPACE no responde.
    down: bool = False
    #: Unidades imputadas, en orden: (cuenta, funcionalidad, unidades).
    consumed: list = field(default_factory=list)

    def use(self, plan: tuple[dict, dict], **limits) -> "FakeSpace":
        self.features, self.limits = dict(plan[0]), {**plan[1], **limits}
        return self

    async def plan(self, account_id: str) -> Plan:
        if self.down:
            raise unavailable()
        return Plan(dict(self.features), dict(self.limits), account_id, self)

    async def consume(self, account_id: str, feature: str, units: float) -> None:
        if self.down:
            raise unavailable()
        if self.exhausted is not None:
            raise quota_exceeded(UNIT_LIMITS[self.exhausted], RESET_AT)
        self.consumed.append((account_id, feature, units))

    async def aclose(self) -> None:
        pass


def with_fake_space(app, space: FakeSpace | None = None):
    """Para las pruebas que construyen su propia aplicación en vez de usar
    `conftest.client`: el mismo doble, Business por omisión."""
    fake = space or FakeSpace()
    app.dependency_overrides[get_space] = lambda: fake
    return app
