"""Contract tests for the MOCForge HTTP API.

Each test states a claim CONTRACT.md makes and checks the service honours it.
Where a number is asserted it is a measured property of the real catalogue
(905 parts in 42151-1, 150 lots, 901 of them with geometry) rather than a
figure copied from the implementation, so a regression in the engine shows up
here instead of being ratified.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import (
    BUILDING_SET,
    MALFORMED_SET,
    MINIFIG_ONLY_SET,
    NO_GEOMETRY_SET,
    SECOND_BUILDING_SET,
    TECHNIC_SET,
    UNKNOWN_SET,
)

V1 = "/api/v1"


def error_of(response: Any) -> dict[str, Any]:
    """The contract's Error body, asserting the envelope on the way through."""
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    return error


# ---------------------------------------------------------------------- health


def test_health_reports_the_catalogue(client: TestClient) -> None:
    response = client.get(f"{V1}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    catalogue = body["catalogue"]
    # Thresholds, not exact counts: the catalogue is refreshed daily and grows.
    assert catalogue["sets"] > 20_000
    assert catalogue["parts"] > 60_000
    # updatedAt must be a real timestamp, not a placeholder string.
    dt.datetime.fromisoformat(catalogue["updatedAt"])


def test_every_response_carries_attribution(client: TestClient) -> None:
    # PRD R4: Rebrickable and LDraw attribution plus the LEGO non-affiliation
    # disclaimer ride on the response, since the contract's shapes have no
    # field for them.
    header = client.get(f"{V1}/health").headers["X-Data-Attribution"]
    assert "Rebrickable" in header
    assert "LDraw" in header
    assert "LEGO is a trademark" in header


def test_unknown_query_field_is_rejected(client: TestClient) -> None:
    # Contract rule 6: unknown query/body fields are rejected, not ignored.
    response = client.get(f"{V1}/health", params={"verbose": "1"})
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


def test_archetypes_lists_the_template_library(client: TestClient) -> None:
    response = client.get(f"{V1}/archetypes")
    assert response.status_code == 200
    archetypes = {a["name"]: a for a in response.json()["archetypes"]}
    assert "building" in archetypes
    building = archetypes["building"]
    assert building["studded"] is True
    assert {"cottage", "tower", "hall"} <= set(building["variants"])
    # A Technic chassis has no studs at all; the client uses this to decide how
    # to describe a design, so it must not be hardcoded to true.
    assert archetypes["technic_chassis"]["studded"] is False


# ---------------------------------------------------------------------- search


def test_search_resolves_a_bare_set_number(client: TestClient) -> None:
    # "Accepts a bare set number (42151) and resolves it to canonical (42151-1)",
    # and an exact set-number match ranks first.
    response = client.get(f"{V1}/sets/search", params={"q": "42151"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["setNum"] == TECHNIC_SET
    assert results[0]["name"] == "Bugatti Bolide"
    assert results[0]["themeName"] == "Technic"
    assert results[0]["numParts"] == 905


def test_search_ranks_exact_number_above_name_matches(client: TestClient) -> None:
    results = client.get(
        f"{V1}/sets/search", params={"q": BUILDING_SET, "limit": 10}
    ).json()["results"]
    assert results[0]["setNum"] == BUILDING_SET


def test_search_by_name_is_ordered_by_piece_count(client: TestClient) -> None:
    results = client.get(
        f"{V1}/sets/search", params={"q": "Bugatti", "limit": 10}
    ).json()["results"]
    assert len(results) > 1
    counts = [r["numParts"] for r in results]
    assert counts == sorted(counts, reverse=True)


def test_search_miss_is_an_empty_list_not_an_error(client: TestClient) -> None:
    response = client.get(f"{V1}/sets/search", params={"q": "zzzznosuchthing"})
    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_search_treats_wildcards_as_literal_text(client: TestClient) -> None:
    # An unescaped '%' in a LIKE pattern would match every set in the
    # catalogue, which would look like a working search and be nonsense.
    assert client.get(f"{V1}/sets/search", params={"q": "%"}).json()["results"] == []


def test_search_requires_q(client: TestClient) -> None:
    assert client.get(f"{V1}/sets/search").status_code == 400
    assert client.get(f"{V1}/sets/search", params={"q": ""}).status_code == 400


@pytest.mark.parametrize("limit", [0, 51, -1])
def test_search_limit_is_bounded(client: TestClient, limit: int) -> None:
    response = client.get(f"{V1}/sets/search", params={"q": "brick", "limit": limit})
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


def test_search_honours_limit(client: TestClient) -> None:
    results = client.get(
        f"{V1}/sets/search", params={"q": "brick", "limit": 3}
    ).json()["results"]
    assert len(results) == 3


# ------------------------------------------------------------------ single set


def test_get_set_returns_every_contract_field(client: TestClient) -> None:
    body = client.get(f"{V1}/sets/{TECHNIC_SET}").json()
    assert set(body) == {
        "setNum",
        "name",
        "year",
        "themeId",
        "themeName",
        "numParts",
        "imgUrl",
    }
    assert body["setNum"] == TECHNIC_SET
    assert body["year"] == 2023
    assert body["themeName"] == "Technic"


def test_get_set_accepts_a_bare_number(client: TestClient) -> None:
    assert client.get(f"{V1}/sets/42151").json()["setNum"] == TECHNIC_SET


def test_get_unknown_set_is_404(client: TestClient) -> None:
    response = client.get(f"{V1}/sets/{UNKNOWN_SET}")
    assert response.status_code == 404
    error = error_of(response)
    assert error["code"] == "set_not_found"
    assert error["detail"]["unresolved"] == [UNKNOWN_SET]


def test_malformed_set_number_is_400_not_404(client: TestClient) -> None:
    # A client that sent junk has a bug; one that asked about a set that does
    # not exist does not. The two must not be answered identically.
    response = client.get(f"{V1}/sets/{MALFORMED_SET}")
    assert response.status_code == 400
    error = error_of(response)
    assert error["code"] == "invalid_request"
    assert error["detail"]["malformed"] == [MALFORMED_SET]


# ------------------------------------------------------------------- inventory


def test_inventory_of_one_set(client: TestClient) -> None:
    response = client.post(f"{V1}/inventory", json={"setNums": [TECHNIC_SET]})
    assert response.status_code == 200
    body = response.json()
    assert body["setNums"] == [TECHNIC_SET]
    # Verified against LEGO's own figures in PRD 2.5: 905 regular parts in 150
    # lots, 901 of them with LDraw geometry. Spares (18 more) are excluded.
    assert body["pieces"] == 905
    assert body["lots"] == 150
    assert body["geometryPieces"] == 901
    assert body["families"]
    assert all(f["pieces"] > 0 for f in body["families"])
    assert all(set(f) == {"family", "pieces", "studs"} for f in body["families"])
    assert body["structuralStuds"] >= 0


def test_inventory_combines_sets(client: TestClient) -> None:
    single = [
        client.post(f"{V1}/inventory", json={"setNums": [s]}).json()["pieces"]
        for s in (BUILDING_SET, SECOND_BUILDING_SET)
    ]
    combined = client.post(
        f"{V1}/inventory", json={"setNums": [SECOND_BUILDING_SET, BUILDING_SET]}
    ).json()
    assert combined["pieces"] == sum(single)
    # Canonical and order-independent: the same two sets in either order are
    # the same inventory.
    assert combined["setNums"] == sorted([BUILDING_SET, SECOND_BUILDING_SET])


def test_inventory_counts_a_duplicate_set_twice(client: TestClient) -> None:
    # Owning two copies of a set is a real inventory, so duplicates are summed
    # rather than silently de-duplicated.
    one = client.post(f"{V1}/inventory", json={"setNums": [SECOND_BUILDING_SET]}).json()
    two = client.post(
        f"{V1}/inventory", json={"setNums": [SECOND_BUILDING_SET] * 2}
    ).json()
    assert two["pieces"] == one["pieces"] * 2


def test_inventory_of_a_set_with_no_regular_parts(client: TestClient) -> None:
    # 0011-2 exists but its inventory is minifigs only. That is an empty
    # inventory, not a missing set, so it is a 200 with zeros.
    response = client.post(f"{V1}/inventory", json={"setNums": [MINIFIG_ONLY_SET]})
    assert response.status_code == 200
    body = response.json()
    assert body["pieces"] == 0
    assert body["lots"] == 0
    assert body["families"] == []


def test_inventory_unknown_set_is_404_with_the_unresolved_list(
    client: TestClient,
) -> None:
    response = client.post(
        f"{V1}/inventory", json={"setNums": [TECHNIC_SET, UNKNOWN_SET]}
    )
    assert response.status_code == 404
    error = error_of(response)
    assert error["code"] == "set_not_found"
    assert error["detail"] == {"unresolved": [UNKNOWN_SET]}


def test_inventory_malformed_set_is_400(client: TestClient) -> None:
    response = client.post(f"{V1}/inventory", json={"setNums": ["../../etc/passwd"]})
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


def test_inventory_rejects_empty_and_oversized_requests(client: TestClient) -> None:
    empty = client.post(f"{V1}/inventory", json={"setNums": []})
    assert empty.status_code == 400
    assert error_of(empty)["code"] == "invalid_request"

    too_many = client.post(
        f"{V1}/inventory", json={"setNums": [BUILDING_SET] * 11}
    )
    assert too_many.status_code == 400
    assert error_of(too_many)["code"] == "invalid_request"


def test_inventory_rejects_unknown_body_field(client: TestClient) -> None:
    # Rule 5 again, on a body: a client posting `setNum` must be told, not
    # handed an answer about a different inventory.
    response = client.post(
        f"{V1}/inventory", json={"setNums": [BUILDING_SET], "includeSpares": True}
    )
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


# -------------------------------------------------------------------- generate


@pytest.fixture(scope="module")
def generated(client: TestClient) -> dict[str, Any]:
    response = client.post(f"{V1}/generate", json={"setNums": [BUILDING_SET]})
    assert response.status_code == 200
    return response.json()


def test_generate_yields_designs(generated: dict[str, Any]) -> None:
    assert generated["designs"], "10696-1 should yield at least one design"
    assert generated["inventory"]["setNums"] == [BUILDING_SET]
    assert generated["elapsedMs"] >= 0


def test_returned_designs_satisfy_the_client_invariant(
    generated: dict[str, Any],
) -> None:
    # "The API never returns a Design that failed validation. confidence is
    # always 1.0 in v1 and a design always has at least 4 parts."
    for design in generated["designs"]:
        assert design["confidence"] == 1.0
        assert design["pieceCount"] >= 4
        assert design["steps"]
        placed = sum(p["quantity"] for step in design["steps"] for p in step["parts"])
        assert placed == design["pieceCount"]
        assert [s["index"] for s in design["steps"]] == list(
            range(1, len(design["steps"]) + 1)
        )
        assert design["ldrUrl"] == f"{V1}/designs/{design['id']}/ldr"
        assert design["variant"] is None or isinstance(design["variant"], str)
        assert isinstance(design["warnings"], list)


def test_designs_are_sorted_by_piece_count_descending(
    generated: dict[str, Any],
) -> None:
    counts = [d["pieceCount"] for d in generated["designs"]]
    assert counts == sorted(counts, reverse=True)


def test_build_steps_name_real_parts_and_colours(generated: dict[str, Any]) -> None:
    step = generated["designs"][0]["steps"][0]
    assert step["level"] >= 0
    for part in step["parts"]:
        assert set(part) == {
            "partNum",
            "partName",
            "colorId",
            "colorName",
            "quantity",
            "note",
        }
        assert part["quantity"] >= 1
        assert part["partName"] != "unknown part"
        assert part["colorName"] and part["colorName"] != "[Unknown]"
        assert part["note"], "every part needs a human label"


def test_designs_only_use_parts_the_user_owns(generated: dict[str, Any]) -> None:
    """The steps must be buildable from the stated inventory, colour included.

    This is the assertion that makes `colorId` worth trusting: the engine now
    carries the Rebrickable colour id through placement, so every
    `(partNum, colorId)` a step names has to be a lot the user actually owns,
    in at least the quantity the step asks for.
    """
    from collections import Counter

    from inventory import Catalogue

    catalogue = Catalogue()
    try:
        owned = catalogue.set_lots(BUILDING_SET)
    finally:
        catalogue.conn.close()

    for design in generated["designs"]:
        used: Counter[tuple[str, int]] = Counter()
        for step in design["steps"]:
            for part in step["parts"]:
                used[(part["partNum"], part["colorId"])] += part["quantity"]
        for (part_num, colour_id), quantity in used.items():
            assert owned.get((part_num, colour_id), 0) >= quantity, (
                f"{design['id']} uses {quantity} of {part_num}/{colour_id}, "
                f"owned {owned.get((part_num, colour_id), 0)}"
            )


def test_every_archetype_is_accounted_for(
    client: TestClient, generated: dict[str, Any]
) -> None:
    # Nothing is dropped silently: each archetype/variant either produced a
    # design or gave a reason it did not.
    known = {a["name"] for a in client.get(f"{V1}/archetypes").json()["archetypes"]}
    seen = {d["archetype"] for d in generated["designs"]}
    seen |= {w["archetype"] for w in generated["withheld"]}
    assert seen == known
    assert all(w["reason"] for w in generated["withheld"])


def test_generate_is_cached_and_deterministic(
    client: TestClient, generated: dict[str, Any]
) -> None:
    started = time.perf_counter()
    again = client.post(f"{V1}/generate", json={"setNums": [BUILDING_SET]}).json()
    elapsed = time.perf_counter() - started
    assert [d["id"] for d in again["designs"]] == [
        d["id"] for d in generated["designs"]
    ]
    # Cold generation for this inventory is seconds of geometry work; a repeat
    # must come from the cache. The bound is loose to stay honest on a busy
    # machine while still failing if the cache is bypassed.
    assert elapsed < 1.5, f"repeat generate took {elapsed:.2f}s; cache not used"


def test_generate_ids_are_stable_for_the_same_triple(
    client: TestClient, generated: dict[str, Any]
) -> None:
    # Same archetype, variant and set numbers -> same id, even though this
    # request asks for one archetype only and so is a different cache entry.
    filtered = client.post(
        f"{V1}/generate", json={"setNums": [BUILDING_SET], "archetypes": ["building"]}
    ).json()
    expected = {
        d["id"] for d in generated["designs"] if d["archetype"] == "building"
    }
    assert {d["id"] for d in filtered["designs"]} == expected
    assert {d["archetype"] for d in filtered["designs"]} == {"building"}
    assert all(w["archetype"] == "building" for w in filtered["withheld"])


def test_generate_technic_fixture(client: TestClient) -> None:
    body = client.post(f"{V1}/generate", json={"setNums": [TECHNIC_SET]}).json()
    assert body["inventory"]["pieces"] == 905
    assert body["designs"], "42151-1 should yield at least one design"
    assert all(d["confidence"] == 1.0 for d in body["designs"])


def test_generate_with_no_buildable_design_is_a_200(client: TestClient) -> None:
    # "May be empty - an inventory with no buildable design is a normal
    # outcome, not an error, and the client must present withheld reasons."
    response = client.post(f"{V1}/generate", json={"setNums": [NO_GEOMETRY_SET]})
    assert response.status_code == 200
    body = response.json()
    assert body["designs"] == []
    assert body["withheld"]
    assert all(len(w["reason"]) > 10 for w in body["withheld"])
    assert body["inventory"]["geometryPieces"] == 0


def test_generate_rejects_an_unknown_archetype(client: TestClient) -> None:
    response = client.post(
        f"{V1}/generate", json={"setNums": [BUILDING_SET], "archetypes": ["mansion"]}
    )
    assert response.status_code == 400
    error = error_of(response)
    assert error["code"] == "invalid_request"
    assert error["detail"]["unknown"] == ["mansion"]


def test_generate_rejects_an_empty_archetype_filter(client: TestClient) -> None:
    response = client.post(
        f"{V1}/generate", json={"setNums": [BUILDING_SET], "archetypes": []}
    )
    assert response.status_code == 400


def test_generate_rejects_unknown_body_field(client: TestClient) -> None:
    response = client.post(
        f"{V1}/generate", json={"setNums": [BUILDING_SET], "maxPieces": 100}
    )
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


def test_generate_unknown_set_is_404(client: TestClient) -> None:
    response = client.post(f"{V1}/generate", json={"setNums": [UNKNOWN_SET]})
    assert response.status_code == 404
    assert error_of(response)["code"] == "set_not_found"


# ------------------------------------------------------------------ design ldr


def test_ldr_download(client: TestClient, generated: dict[str, Any]) -> None:
    design = generated["designs"][0]
    response = client.get(design["ldrUrl"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-disposition"].endswith('.ldr"')

    lines = response.text.strip().splitlines()
    # The model names itself with the name it is downloaded as: the API renders
    # through the engine's writer, which stamps the file's own name into the
    # header, so a temporary render name would end up inside a kept file.
    assert f"0 Name: {design['archetype']}" in response.text
    assert response.headers["content-disposition"].endswith(
        f'filename="{lines[1].removeprefix("0 Name: ")}"'
    )
    # Attribution is inside the file too, because a downloaded model outlives
    # the page that served it (PRD R4/R5).
    header = "\n".join(line for line in lines if line.startswith("0 "))
    assert "Rebrickable" in header
    assert "LDraw" in header
    assert "LEGO is a trademark" in header
    # One type-1 sub-file reference per placed part.
    part_lines = [line for line in lines if line.startswith("1 ")]
    assert len(part_lines) == design["pieceCount"]
    assert all(line.endswith(".dat") for line in part_lines)


def test_ldr_for_an_unknown_id_is_404(client: TestClient) -> None:
    response = client.get(f"{V1}/designs/0000000000000000/ldr")
    assert response.status_code == 404
    assert error_of(response)["code"] == "design_not_found"


def test_ldr_survives_a_repeat_generate(
    client: TestClient, generated: dict[str, Any]
) -> None:
    # A cached generate response must not hand out an ldrUrl that 404s, so the
    # cache re-registers its designs on every hit.
    design_id = generated["designs"][-1]["id"]
    client.post(f"{V1}/generate", json={"setNums": [BUILDING_SET]})
    assert client.get(f"{V1}/designs/{design_id}/ldr").status_code == 200


# ----------------------------------------------------------------- concurrency


def test_concurrent_requests_do_not_trip_sqlite_thread_affinity(
    client: TestClient,
) -> None:
    """The catalogue connection is bound to the thread that opened it.

    `Catalogue` opens SQLite with the default `check_same_thread=True`, so any
    request served on a different thread from the one that built the engine
    would raise `sqlite3.ProgrammingError` - a 500, under load only. This
    fires overlapping requests across several endpoints to prove they are all
    funnelled onto the engine's own thread.
    """
    from concurrent.futures import ThreadPoolExecutor

    calls = [
        lambda: client.get(f"{V1}/sets/search", params={"q": "42151"}),
        lambda: client.post(f"{V1}/inventory", json={"setNums": [TECHNIC_SET]}),
        lambda: client.get(f"{V1}/sets/{BUILDING_SET}"),
        lambda: client.get(f"{V1}/health"),
    ] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = [f.result() for f in [pool.submit(c) for c in calls]]
    assert [r.status_code for r in responses] == [200] * len(calls)


def test_schema_documents_only_statuses_the_service_returns(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            responses = operation["responses"]
            # FastAPI's automatic 422 is removed: validation failures are
            # answered as the contract's 400 invalid_request.
            assert "422" not in responses, f"{method} {path}"
            assert (
                responses["400"]["content"]["application/json"]["schema"]["$ref"]
                == "#/components/schemas/ErrorResponse"
            )
