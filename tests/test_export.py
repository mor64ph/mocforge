"""Tests for the download pack: `export.py` and the v1.1 design endpoints.

Split the way the module is: the layout tests feed a synthetic specification and
throwaway PNGs, so they run in milliseconds and say something about the document
rather than about the catalogue; the rest drive the real engine, because "the
zip contains the model the client was shown" is not a claim a stub can make.

The document tests read the PDF back with pypdf instead of checking that the
bytes begin with `%PDF`. "The attribution is in the document" is a legal
requirement, and a requirement nobody reads back is a requirement nobody has
checked.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import export
from export import (
    DesignSpec,
    PackageFailed,
    PartLine,
    RenderedImages,
    RenderFailed,
    StepSpec,
    aggregate_parts,
    attribution_text,
    builder_notes,
    package,
    parts_csv,
)

DB_PATH = REPO_ROOT / "data" / "mocforge.db"
LDRAW_PATH = REPO_ROOT / "data" / "ldraw" / "complete.zip"

# 10696-1 is the Creative Brick Box: it yields all three building variants, all
# three sculptures and a car, so one generate covers every archetype that can
# reach a document.
BUILDING_SET = "10696-1"

V1 = "/api/v1"


# ------------------------------------------------------------------- fixtures


def png(width: int = 40, height: int = 30, fill: tuple[int, int, int] = (200, 0, 0)) -> bytes:
    """A real PNG, small. reportlab decodes what it is given, so it must be one."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), fill).save(buffer, format="PNG")
    return buffer.getvalue()


def spec(**overrides: Any) -> DesignSpec:
    """A two-step design with the awkward cases a real one contains.

    The part name carries a comma (so the CSV has to quote it), one colour has
    no RGB (so the document has to print the name without a chip), and part
    3004 appears in both steps in the same colour (so the parts list has to sum
    it rather than list it twice).
    """
    fields: dict[str, Any] = {
        "design_id": "0123456789abcdef",
        "title": "MOCForge brick building (cottage)",
        "archetype": "building",
        "variant": "cottage",
        "set_nums": ("10696-1", "10715-1"),
        # As the service supplies them: already translated, and exactly the
        # strings it published on `Design.builderNotes`.
        "notes": builder_notes(
            (
                "optional role 'roof_plate' unfilled; omitted",
                "lowest point is 20.0 LDU vs ground plane 0 (sunk below ground)",
            )
        ),
        "steps": (
            StepSpec(
                index=1,
                level=0.0,
                label="Floor",
                parts=(
                    PartLine("3004", "Brick 1 x 2", "Dark Azure", "078BC9", 6),
                    PartLine("3034", "Plate 2 x 8, with holes", "White", "FFFFFF", 1),
                ),
            ),
            StepSpec(
                index=2,
                level=3.0,
                label="Wall course 1",
                parts=(
                    PartLine("3004", "Brick 1 x 2", "Dark Azure", "078BC9", 4),
                    PartLine("3005", "Brick 1 x 1", "Unnamed Colour", None, 2),
                ),
            ),
        ),
        "ldr_text": "0 cottage\n0 Name: building-cottage.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 3004.dat\n",
        "ldr_filename": "building-cottage_10696-1+10715-1.ldr",
    }
    fields.update(overrides)
    return DesignSpec(**fields)


def images_for(design: DesignSpec) -> RenderedImages:
    return RenderedImages(
        model=png(80, 60, (30, 90, 40)),
        steps=tuple(png(fill=(10 * step.index, 80, 120)) for step in design.steps),
    )


def pdf_pages(document: bytes) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() for page in PdfReader(io.BytesIO(document)).pages]


def members(zip_bytes: bytes) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.testzip() is None, "the archive is corrupt"
        return {info.filename: info for info in archive.infolist()}


def read_member(zip_bytes: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        return archive.read(name)


# ------------------------------------------------------------------ the csv


def test_csv_has_the_four_columns_in_the_stated_order() -> None:
    header = parts_csv(spec()).decode("utf-8-sig").splitlines()[0]
    assert header == "Part number,Part name,Colour,Quantity"


def test_csv_sums_a_lot_that_appears_in_more_than_one_step() -> None:
    rows = list(csv.DictReader(io.StringIO(parts_csv(spec()).decode("utf-8-sig"))))
    azure = [row for row in rows if row["Part number"] == "3004"]
    assert len(azure) == 1, "a lot used in two steps must appear once in the list"
    assert azure[0]["Quantity"] == "10"
    assert sum(int(row["Quantity"]) for row in rows) == spec().pieces


def test_csv_quotes_a_part_name_containing_a_comma() -> None:
    text = parts_csv(spec()).decode("utf-8-sig")
    assert '"Plate 2 x 8, with holes"' in text
    rows = list(csv.reader(io.StringIO(text)))
    assert all(len(row) == 4 for row in rows), "a stray comma split a row"


def test_csv_is_written_for_the_spreadsheet_that_will_open_it() -> None:
    raw = parts_csv(spec())
    # A BOM, because Excel on Windows reads BOM-less UTF-8 as the system
    # codepage, and CRLF per RFC 4180.
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_parts_list_is_ordered_by_descending_quantity() -> None:
    quantities = [part.quantity for part in aggregate_parts(spec())]
    assert quantities == sorted(quantities, reverse=True)


# ---------------------------------------------------------------- attribution


@pytest.mark.parametrize(
    "required",
    [
        "Rebrickable",
        "LDraw Parts Library",
        "CC BY 2.0",
        "trademark of the LEGO Group",
        "does not sponsor",
        "rebrickable.com",
        "library.ldraw.org",
    ],
)
def test_attribution_file_carries_every_required_notice(required: str) -> None:
    # PRD R4/R5. The pack outlives the page that served it, so the notice has
    # to be inside the pack.
    assert required in attribution_text(spec()).decode("utf-8")


def test_attribution_file_names_the_inventory_the_design_came_from() -> None:
    text = attribution_text(spec()).decode("utf-8")
    assert "10696-1, 10715-1" in text


# -------------------------------------------------------------- the document


def test_document_is_a_cover_a_parts_list_and_a_page_for_every_step() -> None:
    design = spec()
    pages = pdf_pages(export.instructions_pdf(design, images_for(design)))
    assert len(pages) == 2 + len(design.steps)
    assert "MOCFORGE BUILD INSTRUCTIONS" in pages[0]
    assert "Parts you will need" in pages[1]
    assert "STEP 1 OF 2" in pages[2]
    assert "STEP 2 OF 2" in pages[3]


def test_every_step_page_itemises_the_parts_added_at_that_step() -> None:
    design = spec()
    pages = pdf_pages(export.instructions_pdf(design, images_for(design)))
    for step, page in zip(design.steps, pages[2:], strict=True):
        assert step.label in page
        for part in step.parts:
            assert part.part_num in page
            assert part.part_name in page
            assert part.colour_name in page
            assert str(part.quantity) in page


def test_document_states_the_attribution_on_the_cover_and_every_page() -> None:
    design = spec()
    pages = pdf_pages(export.instructions_pdf(design, images_for(design)))
    assert "LEGO® is a trademark of the LEGO Group" in pages[0]
    assert "Rebrickable" in pages[0] and "CC BY 2.0" in pages[0]
    for page in pages:
        assert "Rebrickable" in page, "the running footer carries the notice"


# ------------------------------------------------------------ builder notes


def test_an_optional_part_left_off_is_explained_in_plain_words() -> None:
    notes = builder_notes(["optional role 'roof_plate' unfilled; omitted"])
    assert len(notes) == 1
    assert "roof plate" in notes[0]
    assert "complete without it" in notes[0]
    # None of the engine's own vocabulary survives.
    assert "role" not in notes[0]
    assert "unfilled" not in notes[0]


def test_a_diagnostic_with_no_meaning_for_a_builder_is_dropped() -> None:
    # It measures the model against the internal coordinate frame's floor,
    # which a real model cannot be below, and reads as a fault in a document
    # whose job is to get someone building.
    assert builder_notes(
        ["lowest point is 20.0 LDU vs ground plane 0 (sunk below ground)"]
    ) == ()


def test_an_unrecognised_warning_is_dropped_rather_than_printed_raw() -> None:
    # The next archetype will invent a warning this module has never seen. It
    # must not reach the page: untranslated engineering text is worse than a
    # missing note, and the text is still on `Design.warnings` over the API.
    assert builder_notes(["spine offset 3 LDU from lattice origin"]) == ()


def test_the_same_note_twice_is_said_once() -> None:
    twice = ["optional role 'roof_plate' unfilled; omitted"] * 2
    assert len(builder_notes(twice)) == 1


def test_the_cover_never_prints_engine_diagnostics() -> None:
    cover = pdf_pages(export.instructions_pdf(spec(), images_for(spec())))[0]
    # The builder-facing translation is there...
    assert "Before you start" in cover
    assert "optional roof plate" in cover
    # ...and none of the text the engine actually recorded.
    for leak in ("LDU", "ground plane", "sunk below", "optional role", "unfilled"):
        assert leak not in cover, f"{leak!r} reached the page"


def test_the_cover_prints_the_published_notes_verbatim() -> None:
    # The document renders `Design.builderNotes` as served, so the page and the
    # printed page cannot disagree about a design's caveats. Translating a
    # second time here is exactly what this arrangement rules out.
    design = spec(notes=("Leave the chimney off: nothing in your box fits it.",))
    cover = pdf_pages(export.instructions_pdf(design, images_for(design)))[0]
    assert "Leave the chimney off: nothing in your box fits it." in cover


def test_a_design_with_no_notes_shows_no_notes_section() -> None:
    quiet = spec(notes=())
    cover = pdf_pages(export.instructions_pdf(quiet, images_for(quiet)))[0]
    assert "Before you start" not in cover


def test_document_explains_the_ghosting_convention_once() -> None:
    design = spec()
    pages = pdf_pages(export.instructions_pdf(design, images_for(design)))
    assert "grey pieces are ones you have already placed" in pages[2]
    assert "already placed" not in pages[3], "the legend belongs on the first step only"


def test_a_step_larger_than_one_page_continues_on_the_next() -> None:
    # A one-layer mosaic really is a single step of 40 lots, so spilling is a
    # normal case for some designs rather than a guard against a freak one.
    crowded = spec(
        steps=(
            StepSpec(
                index=1,
                level=0.0,
                label="Mosaic layer",
                parts=tuple(
                    PartLine(f"30{n:02d}", f"Brick 1 x {n}", "Red", "C91A09", 1)
                    for n in range(40)
                ),
            ),
        )
    )
    pages = pdf_pages(export.instructions_pdf(crowded, images_for(crowded)))
    assert len(pages) > 3, "40 lots cannot fit under a step image on one page"
    assert "Step 1 of 1 (continued)" in pages[-1]
    assert "PART NUMBER" in pages[-1], "a spilled table repeats its heading"
    for n in range(40):
        assert f"Brick 1 x {n}" in "".join(pages[2:])


def test_one_brick_used_twice_in_a_step_is_one_row() -> None:
    # The engine itemises a lot per (part, colour, note), so the same brick can
    # arrive twice in one step under two notes. Printed as two rows of 1 it
    # reads as a mistake; the builder needs one row of 2, and the step heading
    # already carries both notes.
    design = spec(
        steps=(
            StepSpec(
                index=1,
                level=2.25,
                label="Rear axle; front axle",
                parts=(
                    PartLine("4600", "Plate Special 2 x 2 with Wheel Holders",
                             "Black", "05131D", 1),
                    PartLine("4600", "Plate Special 2 x 2 with Wheel Holders",
                             "Black", "05131D", 1),
                ),
            ),
        )
    )
    step = design.steps[0]
    assert [(part.part_num, part.quantity) for part in step.lots] == [("4600", 2)]
    page = pdf_pages(export.instructions_pdf(design, images_for(design)))[2]
    assert "2 pieces in 1 lot" in page
    assert page.count("4600") == 1


def test_a_part_name_too_long_for_its_column_is_clipped_not_dropped() -> None:
    design = spec(
        steps=(
            StepSpec(
                index=1,
                level=0.0,
                label="Floor",
                parts=(
                    PartLine(
                        "11213",
                        "Plate Special 6 x 6 with Four Studs on Side and Bottom Tubes",
                        "Light Bluish Gray",
                        "A0A5A9",
                        1,
                    ),
                ),
            ),
        )
    )
    page = pdf_pages(export.instructions_pdf(design, images_for(design)))[2]
    assert "Plate Special 6 x 6" in page
    assert "..." in page


@pytest.mark.parametrize(
    "kind",
    [
        ("building", "hall"),
        # The name that broke it: at the display size `technic_crib/tower`
        # overflowed its stat cell and rendered as `technic_crib/...`.
        ("technic_crib", "tower"),
        ("an_archetype_with_a_truly_immoderate_name", "and_a_long_variant_too"),
    ],
)
def test_the_design_stat_is_never_clipped_however_long_its_name(
    kind: tuple[str, str],
) -> None:
    archetype, variant = kind
    design = spec(archetype=archetype, variant=variant)
    cover = pdf_pages(export.instructions_pdf(design, images_for(design)))[0]
    # Both halves of the name are present, and no ellipsis stands in for a
    # value: the cell shrinks and then wraps rather than truncating.
    assert archetype in cover
    assert variant in cover
    assert "..." not in cover
    # And when it does wrap, the archetype is the line above its variant -
    # drawing the stack bottom-up had them the wrong way round at first.
    assert cover.index(archetype) < cover.index(variant)


def test_a_colour_with_no_rgb_still_names_itself() -> None:
    # A handful of catalogue colours carry no RGB. Printing the name without a
    # chip is right; printing a guessed chip would be a lie about the brick.
    page = pdf_pages(export.instructions_pdf(spec(), images_for(spec())))[3]
    assert "Unnamed Colour" in page


# --------------------------------------------------------------- the zip file


def test_package_holds_exactly_the_promised_files() -> None:
    design = spec()
    export_ = package(design, images_for(design))
    assert set(members(export_.zip_bytes)) == {
        "instructions.pdf",
        "parts-list.csv",
        "ATTRIBUTION.txt",
        "finished-model.png",
        design.ldr_filename,
        "steps/step-01.png",
        "steps/step-02.png",
    }
    assert export_.filename == "mocforge_building-cottage_10696-1+10715-1.zip"


def test_package_keeps_the_ldraw_model_verbatim() -> None:
    design = spec()
    kept = read_member(package(design, images_for(design)).zip_bytes, design.ldr_filename)
    assert kept.decode("utf-8") == design.ldr_text


def test_package_stores_images_and_compresses_text() -> None:
    design = spec()
    entries = members(package(design, images_for(design)).zip_bytes)
    # A PNG is already deflated; deflating it again costs CPU for nothing.
    assert entries["steps/step-01.png"].compress_type == zipfile.ZIP_STORED
    assert entries["parts-list.csv"].compress_type == zipfile.ZIP_DEFLATED


def test_the_same_design_packages_to_the_same_bytes() -> None:
    # A design id is a hash of (archetype, variant, setNums) and is stable
    # across restarts, so the package it names is built without a clock in it.
    # That is what makes the service's cache verifiable rather than plausible.
    design = spec()
    first = package(design, images_for(design)).zip_bytes
    second = package(design, images_for(design)).zip_bytes
    assert first == second


def test_step_images_that_do_not_match_the_steps_are_refused() -> None:
    design = spec()
    truncated = RenderedImages(model=png(), steps=(png(),))
    with pytest.raises(PackageFailed, match="disagree about how this design is stepped"):
        package(design, truncated)


def test_an_unreadable_image_is_a_package_failure_not_a_crash() -> None:
    design = spec()
    corrupt = RenderedImages(model=b"not a png at all", steps=images_for(design).steps)
    with pytest.raises(PackageFailed):
        package(design, corrupt)


# -------------------------------------------------- the engine and the service


@pytest.fixture(scope="module")
def client() -> Iterator[Any]:
    """One TestClient for the module: the lifespan loads the whole engine.

    Integration on purpose, like tests/api. The interesting claims here - that
    the zip holds the same model the client was shown, that a step image ghosts
    what came before it - do not exist against a stub engine.
    """
    if not DB_PATH.exists() or not LDRAW_PATH.exists():
        pytest.skip(
            f"needs the built data files: {DB_PATH} and {LDRAW_PATH}. "
            "Run `python ingest.py build` and download the LDraw library."
        )
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def designs(client: Any) -> list[dict[str, Any]]:
    response = client.post(f"{V1}/generate", json={"setNums": [BUILDING_SET]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["designs"]) >= 3, "10696-1 should yield several designs"
    return list(body["designs"])


def error_of(response: Any) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]


def test_the_service_publishes_builder_notes_beside_raw_warnings(
    designs: list[dict[str, Any]],
) -> None:
    # CONTRACT.md v1.2: both fields are present on every design, and
    # `builderNotes` is the translation of `warnings` rather than a copy of it.
    for design in designs:
        assert isinstance(design["warnings"], list)
        assert isinstance(design["builderNotes"], list)
        assert design["builderNotes"] == list(builder_notes(design["warnings"]))
        for note in design["builderNotes"]:
            assert "LDU" not in note


def test_the_pack_repeats_the_notes_the_client_was_given(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    """The page and the PDF must not be able to state different caveats.

    Both render `Design.builderNotes` verbatim, so this checks the document
    against the very field the client holds - not against a second translation.
    """
    design = designs[0]
    zip_bytes = client.get(f"{V1}/designs/{design['id']}/package").content
    cover = pdf_pages(read_member(zip_bytes, "instructions.pdf"))[0]
    for note in design["builderNotes"]:
        assert note in cover
    if not design["builderNotes"]:
        assert "Before you start" not in cover
    # Whatever the engine recorded stays out of the document either way.
    for warning in design["warnings"]:
        assert warning not in cover


def test_health_advertises_the_v1_1_features(client: Any) -> None:
    # CONTRACT.md makes /health the discovery mechanism: a client must not call
    # these endpoints until the service names them.
    features = client.get(f"{V1}/health").json()["features"]
    assert {"designImage", "stepImage", "designPackage"} <= set(features)


def test_the_pack_is_a_zip_named_after_the_design(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    design = designs[0]
    response = client.get(f"{V1}/designs/{design['id']}/package")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=\"mocforge_")
    assert disposition.endswith('.zip"')
    # PRD R4: attribution rides on every response as well as inside the pack.
    assert "Rebrickable" in response.headers["X-Data-Attribution"]


def test_the_pack_describes_the_design_the_client_was_shown(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    design = designs[0]
    zip_bytes = client.get(f"{V1}/designs/{design['id']}/package").content
    entries = members(zip_bytes)

    assert sum(1 for name in entries if name.startswith("steps/")) == len(design["steps"])
    assert "instructions.pdf" in entries

    rows = list(
        csv.DictReader(
            io.StringIO(read_member(zip_bytes, "parts-list.csv").decode("utf-8-sig"))
        )
    )
    assert sum(int(row["Quantity"]) for row in rows) == design["pieceCount"]

    ldr = client.get(design["ldrUrl"]).text
    kept = next(name for name in entries if name.endswith(".ldr"))
    assert read_member(zip_bytes, kept).decode("utf-8") == ldr

    pages = pdf_pages(read_member(zip_bytes, "instructions.pdf"))
    # At least: a cover, a parts list and one page per step. A crowded step or a
    # long parts list spills onto continuation pages, which is why this is a
    # lower bound.
    assert len(pages) >= 2 + len(design["steps"])
    assert f"STEP {len(design['steps'])} OF {len(design['steps'])}" in "".join(pages)
    assert str(design["pieceCount"]) in pages[0]


def test_the_pack_is_built_once_and_served_from_cache(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    design = designs[1]
    first = client.get(f"{V1}/designs/{design['id']}/package")
    second = client.get(f"{V1}/designs/{design['id']}/package")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_the_finished_model_image_is_a_png(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    response = client.get(f"{V1}/designs/{designs[0]['id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_step_images_differ_because_each_one_adds_parts(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    design = next(d for d in designs if len(d["steps"]) >= 2)
    first = client.get(f"{V1}/designs/{design['id']}/steps/1/image")
    second = client.get(f"{V1}/designs/{design['id']}/steps/2/image")
    assert first.status_code == second.status_code == 200
    assert first.content.startswith(b"\x89PNG")
    assert first.content != second.content


def test_the_step_image_matches_the_one_in_the_pack(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    # One render pass answers all three endpoints, so the endpoint and the pack
    # cannot show different pictures of the same step.
    design = designs[0]
    served = client.get(f"{V1}/designs/{design['id']}/steps/1/image").content
    zip_bytes = client.get(f"{V1}/designs/{design['id']}/package").content
    assert read_member(zip_bytes, "steps/step-01.png") == served


@pytest.mark.parametrize("path", ["package", "image", "steps/1/image"])
def test_an_unknown_design_is_a_404_on_every_artefact(client: Any, path: str) -> None:
    response = client.get(f"{V1}/designs/0000000000000000/{path}")
    assert response.status_code == 404
    assert error_of(response)["code"] == "design_not_found"


@pytest.mark.parametrize("index", [0, -1, 999])
def test_an_index_outside_the_design_is_step_not_found(
    client: Any, designs: list[dict[str, Any]], index: int
) -> None:
    # A real design asked a wrong question, which is not the same as a
    # malformed request: 0 and 999 are both 404 step_not_found.
    response = client.get(f"{V1}/designs/{designs[0]['id']}/steps/{index}/image")
    assert response.status_code == 404
    assert error_of(response)["code"] == "step_not_found"


def test_a_non_numeric_step_index_is_a_400(
    client: Any, designs: list[dict[str, Any]]
) -> None:
    response = client.get(f"{V1}/designs/{designs[0]['id']}/steps/third/image")
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


@pytest.mark.parametrize("path", ["package", "image"])
def test_unknown_query_fields_are_rejected(
    client: Any, designs: list[dict[str, Any]], path: str
) -> None:
    # Contract rule 6, which applies to the new endpoints too.
    response = client.get(
        f"{V1}/designs/{designs[0]['id']}/{path}", params={"format": "pdf"}
    )
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"


def test_a_design_the_renderer_cannot_draw_is_a_render_failure(
    client: Any, designs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed render, a failed package and an unknown id are three answers.

    `build_export` resolves `render_design` out of the export module's globals
    at call time, so patching it there is what the engine actually calls.
    """

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RenderFailed("the projection collapsed")

    monkeypatch.setattr(export, "render_design", explode)
    # A design no other test has packaged, so the cache cannot answer instead.
    response = client.get(f"{V1}/designs/{designs[-1]['id']}/package")
    assert response.status_code == 500
    assert error_of(response)["code"] == "render_failed"


def test_a_pack_that_cannot_be_written_is_a_package_failure(
    client: Any, designs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(export, "instructions_pdf", explode)
    response = client.get(f"{V1}/designs/{designs[-2]['id']}/package")
    assert response.status_code == 500
    assert error_of(response)["code"] == "package_failed"


def test_rendering_nothing_is_a_render_failure(client: Any) -> None:
    # The renderer refuses an empty build, and that refusal has to arrive as a
    # render failure rather than as a bare ValueError from inside PIL.
    from generate import Build

    from render import Renderer

    assert client is not None  # the fixture guarantees the data files exist
    with pytest.raises(RenderFailed):
        export.render_design(Build(template="empty"), Renderer())
