"""Cabeceras de identidad tal como las escribe la pasarela.

`space-auth` las borra incondicionalmente y escribe cada una una sola vez, tras
validar canal y rol contra estos mismos conjuntos: una petición sin ellas, con
un valor de fuera o con una repetida no puede venir de Kong. Por eso las pruebas
las suministran: el servicio confía en ellas, luego la prueba ha de ponerlas.
"""

ACCOUNT_A = "66f0000000000000000000a1"
ACCOUNT_B = "66f0000000000000000000b2"

ABSENT = object()


def gateway_headers(
    account_id=ACCOUNT_A,
    channel="frontend",
    role="CUSTOMER",
    extra: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Lista de pares, no diccionario: así puede llevar cabeceras repetidas.

    `ABSENT` omite esa cabecera.
    """
    headers = []
    if account_id is not ABSENT:
        headers.append(("X-Account-Id", account_id))
    if channel is not ABSENT:
        headers.append(("X-Auth-Channel", channel))
    if role is not ABSENT:
        headers.append(("X-Role", role))
    return headers + (extra or [])
