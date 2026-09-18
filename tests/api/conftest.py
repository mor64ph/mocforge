"""Shared fixtures for the API tests.

These are integration tests on purpose. The interesting failure modes of this
service - a bare set number resolving to the wrong version, an inventory whose
parts have no geometry, an archetype that withholds - only exist against the
real catalogue and the real LDraw library, and a mocked `Catalogue` would test
the mock. The cost is that the suite needs the built data files, so it skips
with an explanation rather than failing if they are absent.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # imported lazily in the fixture so a missing httpx is a skip
    from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "mocforge.db"
LDRAW_PATH = REPO_ROOT / "data" / "ldraw" / "complete.zip"

# Real set numbers, with the properties each one is used for.
TECHNIC_SET = "42151-1"       # Bugatti Bolide, 905 parts, 150 lots
BUILDING_SET = "10696-1"      # Creative Brick Box, yields several buildings
SECOND_BUILDING_SET = "10715-1"  # Bricks on a Roll, 442 parts
MINIFIG_ONLY_SET = "0011-2"   # exists, but its inventory holds only minifigs
NO_GEOMETRY_SET = "TRADINGCARD-6"  # 315 trading cards, no LDraw geometry at all
UNKNOWN_SET = "99999999-1"
MALFORMED_SET = "42151!"


@pytest.fixture(scope="session")
def client() -> Iterator["TestClient"]:
    """One TestClient for the whole session.

    Session-scoped because the lifespan loads the catalogue and the LDraw index,
    which takes a couple of seconds; per-test clients would pay that per test
    and would also hide the fact that the engine is shared state.
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
