"""listGalleries — GET /v1/fr/commercial/galleries (F3, #35).

Toda respuesta pasa por el arnés (`tests/support/galleries.checked`).

ORDEN: `_id` ascendente, el de creación; el porqué, en
`src/controllers/galleries.list_galleries`. Se prueba con nombres en orden
alfabético INVERSO, para que un listado por `name` no pueda pasar por bueno.

COTAS: un caso negativo por cada parámetro acotado, emparejado con el valor en
el borde, que da `200`. Nunca se acota en silencio.
"""

import pytest

from tests.support.galleries import assert_bad_request, create, fetch, listing
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import CREATED, seed_gallery


def created(client, names, account=ACCOUNT_A):
    return [create(client, {"name": name}, gateway_headers(account)).json()["id"] for name in names]


# ---------------------------------------------------------------- 200


def test_200_an_account_without_galleries_gets_an_empty_first_page(client, db):
    res = listing(client)

    assert res.status_code == 200, res.text
    assert res.json() == {"items": [], "page": 1, "pageSize": 20, "total": 0}


def test_200_lists_only_the_galleries_of_the_account(client, db):
    mine = created(client, ["a1", "a2"], ACCOUNT_A)
    created(client, ["b1", "b2", "b3"], ACCOUNT_B)

    body = listing(client, headers=gateway_headers(ACCOUNT_A)).json()

    assert [g["id"] for g in body["items"]] == mine
    assert body["total"] == 2


def test_200_an_admin_lists_only_its_own_account_too(client, db):
    created(client, ["b1"], ACCOUNT_B)

    body = listing(client, headers=gateway_headers(ACCOUNT_A, role="ADMIN")).json()

    assert (body["items"], body["total"]) == ([], 0)


def test_200_the_items_are_the_full_galleries_as_get_returns_them(client, db):
    # Una congelada con todos los opcionales: el arnés valida cada elemento.
    seed_gallery(
        db,
        ACCOUNT_A,
        status="frozen",
        frozenReason="MODEL_NOT_IN_PLAN",
        description="d",
        updatedAt=CREATED,
    )
    gallery_id = created(client, ["nueva"])[0]

    items = listing(client).json()["items"]

    assert items[0]["frozenReason"] == "MODEL_NOT_IN_PLAN"
    assert items[1] == fetch(client, gallery_id).json()
    assert all("accountId" not in g and "_id" not in g for g in items)


def test_200_pagination_is_stable_by_creation_order_not_by_name(client, db):
    ids = created(client, ["e", "d", "c", "b", "a"])

    pages = [listing(client, {"page": p, "pageSize": 2}).json() for p in (1, 2, 3)]

    assert [[g["id"] for g in p["items"]] for p in pages] == [ids[0:2], ids[2:4], ids[4:5]]
    assert [p["total"] for p in pages] == [5, 5, 5]
    assert [(p["page"], p["pageSize"]) for p in pages] == [(1, 2), (2, 2), (3, 2)]


def test_200_the_same_query_returns_the_same_page(client, db):
    created(client, ["c", "b", "a"])

    first = listing(client, {"page": 2, "pageSize": 1}).json()
    again = listing(client, {"page": 2, "pageSize": 1}).json()

    assert first == again


@pytest.mark.parametrize("page", [4, 10**30], ids=["justo después del final", "inalcanzable"])
def test_200_a_page_past_the_end_is_empty_with_the_real_total(client, db, page):
    created(client, ["a", "b", "c"])

    res = listing(client, {"page": page, "pageSize": 1})

    assert res.status_code == 200, res.text
    assert res.json() == {"items": [], "page": page, "pageSize": 1, "total": 3}


# --------------------------------------------- cotas: 400 fuera, 200 en el borde

OUT_OF_RANGE = {
    "page=0": {"page": 0},
    "page=-1": {"page": -1},
    "pageSize=0": {"pageSize": 0},
    "pageSize=101": {"pageSize": 101},
    "page no entero": {"page": "abc"},
    "pageSize no entero": {"pageSize": "1.5"},
}

AT_THE_BOUND = {
    "page=1": {"page": 1},
    "pageSize=1": {"pageSize": 1},
    "pageSize=100": {"pageSize": 100},
}


@pytest.mark.parametrize("params", OUT_OF_RANGE.values(), ids=OUT_OF_RANGE.keys())
def test_400_a_parameter_out_of_range_is_rejected_not_clamped(client, db, params):
    assert_bad_request(listing(client, params))


@pytest.mark.parametrize("params", AT_THE_BOUND.values(), ids=AT_THE_BOUND.keys())
def test_200_the_same_parameter_at_the_bound_is_honoured(client, db, params):
    created(client, ["a"])

    body = listing(client, params).json()

    assert body["page"] == params.get("page", 1)
    assert body["pageSize"] == params.get("pageSize", 20)
    assert body["total"] == 1
