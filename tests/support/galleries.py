"""Llamadas a las operaciones de galerías, TODAS a través del arnés.

`checked()` valida cada respuesta contra el contrato y, si es un error, exige la
forma desnuda (`code` y `message`). Las pruebas de F3 (#35) no obtienen una
respuesta sin pasar por aquí, como las de `getGallery` con su `get()`.
"""

from tests.contract.harness import assert_bare_error, assert_satisfies_contract
from tests.support.identity import ACCOUNT_A, gateway_headers

GALLERIES = "/v1/fr/commercial/galleries"
ONE = GALLERIES + "/{}"

#: Campos que lleva siempre una galería; los opcionales se omiten si no hay valor.
REQUIRED = {"id", "name", "model", "status", "identityCount", "faceCount", "createdAt"}


def checked(response):
    assert_satisfies_contract(response)
    if response.status_code >= 400:
        assert_bare_error(response)
    return response


def create(client, body, headers=None):
    return checked(client.post(GALLERIES, json=body, headers=headers or gateway_headers()))


def listing(client, params=None, headers=None):
    return checked(client.get(GALLERIES, params=params, headers=headers or gateway_headers()))


def fetch(client, gallery_id, headers=None):
    return checked(client.get(ONE.format(gallery_id), headers=headers or gateway_headers()))


def update(client, gallery_id, body, headers=None):
    return checked(
        client.patch(ONE.format(gallery_id), json=body, headers=headers or gateway_headers())
    )


def delete(client, gallery_id, headers=None):
    return checked(client.delete(ONE.format(gallery_id), headers=headers or gateway_headers()))


def assert_owner_sees_it(client, gallery_id, account_id=ACCOUNT_A):
    res = fetch(client, gallery_id, gateway_headers(account_id))
    assert res.status_code == 200, res.text
    assert res.json()["id"] == gallery_id
    return res.json()


def assert_not_found(response):
    assert response.status_code == 404, response.text
    assert response.json() == {"code": "NOT_FOUND", "message": "Gallery not found"}


def assert_bad_request(response):
    assert response.status_code == 400, response.text
    assert response.json() == {"code": "BAD_REQUEST", "message": "Invalid request"}


def assert_name_conflict(response):
    assert response.status_code == 409, response.text
    assert response.json() == {
        "code": "GALLERY_NAME_ALREADY_EXISTS",
        "message": "A gallery with that name already exists",
    }
