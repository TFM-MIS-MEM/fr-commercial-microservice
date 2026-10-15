"""listIdentities — GET …/galleries/{galleryId}/identities (F4, #36).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

El mismo patrón que `listGalleries`: `_id` ascendente, `total` en paralelo y
`400` fuera de cota, nunca acotado en silencio. Se prueba con nombres en orden
alfabético INVERSO, para que un listado por `fullName` no pase por bueno. El
precio de ese orden con `{galleryId}` está en TFM-MIS-MEM/docs#123.
"""

import pytest
from bson import ObjectId

from tests.support.identities import assert_bad_request, assert_not_found, fetch, listing
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A)


def seeded(db, gallery_id, names):
    return [seed_identity_with_faces(db, gallery_id, faces=1, fullName=n) for n in names]


# ---------------------------------------------------------------- 200


def test_200_a_gallery_without_identities_gets_an_empty_first_page(client, db, gallery):
    res = listing(client, gallery)

    assert res.status_code == 200, res.text
    assert res.json() == {"items": [], "page": 1, "pageSize": 20, "total": 0}


def test_200_lists_only_the_identities_of_the_gallery(client, db, gallery):
    mine = seeded(db, gallery, ["c", "b"])
    seeded(db, seed_gallery(db, ACCOUNT_A, name="Otra"), ["a"])

    body = listing(client, gallery).json()

    assert [i["id"] for i in body["items"]] == mine
    assert body["total"] == 2


def test_200_the_items_are_the_identities_as_get_returns_them(client, db, gallery):
    seed_identity_with_faces(db, gallery, faces=1, externalId="E-1")
    seeded(db, gallery, ["b"])

    items = listing(client, gallery).json()["items"]

    assert items == [fetch(client, gallery, i["id"]).json() for i in items]
    assert "externalId" not in items[1]


def test_200_pagination_is_stable_by_creation_order_not_by_name(client, db, gallery):
    ids = seeded(db, gallery, ["e", "d", "c", "b", "a"])

    pages = [listing(client, gallery, {"page": p, "pageSize": 2}).json() for p in (1, 2, 3)]

    assert [[i["id"] for i in p["items"]] for p in pages] == [ids[0:2], ids[2:4], ids[4:5]]
    assert [p["total"] for p in pages] == [5, 5, 5]


@pytest.mark.parametrize("page", [4, 10**30], ids=["justo después del final", "inalcanzable"])
def test_200_a_page_past_the_end_is_empty_with_the_real_total(client, db, gallery, page):
    seeded(db, gallery, ["a", "b", "c"])

    res = listing(client, gallery, {"page": page, "pageSize": 1})

    assert res.json() == {"items": [], "page": page, "pageSize": 1, "total": 3}


# --------------------------------------------- cotas: 400 fuera, 200 en el borde

OUT_OF_RANGE = {
    "page=0": {"page": 0},
    "pageSize=0": {"pageSize": 0},
    "pageSize=101": {"pageSize": 101},
    "page no entero": {"page": "abc"},
}

AT_THE_BOUND = {
    "page=1": {"page": 1},
    "pageSize=1": {"pageSize": 1},
    "pageSize=100": {"pageSize": 100},
}


@pytest.mark.parametrize("params", OUT_OF_RANGE.values(), ids=OUT_OF_RANGE.keys())
def test_400_a_parameter_out_of_range_is_rejected_not_clamped(client, db, gallery, params):
    assert_bad_request(listing(client, gallery, params))


@pytest.mark.parametrize("params", AT_THE_BOUND.values(), ids=AT_THE_BOUND.keys())
def test_200_the_same_parameter_at_the_bound_is_honoured(client, db, gallery, params):
    seeded(db, gallery, ["a"])

    body = listing(client, gallery, params).json()

    assert (body["page"], body["pageSize"]) == (params.get("page", 1), params.get("pageSize", 20))
    assert body["total"] == 1


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, role):
    seeded(db, gallery, ["a"])

    assert_not_found(
        listing(client, gallery, headers=gateway_headers(ACCOUNT_B, role=role)), "Gallery"
    )
    assert listing(client, gallery).json()["total"] == 1


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, db, gallery_id):
    assert_not_found(listing(client, gallery_id), "Gallery")
