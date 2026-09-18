"""Rebrickable v3 API client.

The sanctioned programmatic channel for everything the bulk dumps omit. See PRD
section 2.4 for why this exists instead of a scraper.

Compliance:
  R1  Only https://rebrickable.com/api/v3/ is requested. Asserted per call.
      HTML pages are never fetched.
  R3  MOC designs are never ingested. set_alternates() returns listing metadata
      for linking out only.

Auth: put a free key in the REBRICKABLE_API_KEY env var, or in data/api/key.txt
(gitignored). Get one at https://rebrickable.com/api/ while signed in.

Responses are cached in the api_cache table indefinitely, because catalogue data
is effectively immutable. Pass refresh=True to bypass.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mocforge.db"
KEY_FILE = ROOT / "data" / "api" / "key.txt"

API_PREFIX = "https://rebrickable.com/api/v3/"
MIN_INTERVAL = 1.0  # seconds between requests; be a polite client
USER_AGENT = "MOCForge/0.1 (+personal project; contact via repo owner)"


class MissingKey(RuntimeError):
    pass


def load_key() -> str:
    key = os.environ.get("REBRICKABLE_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise MissingKey(
            "No Rebrickable API key found.\n"
            f"  Set REBRICKABLE_API_KEY, or write the key to {KEY_FILE}\n"
            "  Get a free key at https://rebrickable.com/api/ while signed in.\n"
            "  Everything in Phases 1-2 works without a key; the API only adds\n"
            "  external_ids and user-collection import (PRD 2.4)."
        )
    return key


class RebrickableAPI:
    def __init__(self, key: str | None = None, db_path: Path = DB_PATH):
        self.key = key or load_key()
        self.db_path = db_path
        self._last_call = 0.0
        self._init_cache()

    def _init_cache(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS api_cache (
                     cache_key TEXT PRIMARY KEY, body TEXT NOT NULL,
                     fetched_at TEXT NOT NULL)"""
            )

    # ------------------------------------------------------------------ core

    def get(self, path: str, refresh: bool = False, **params) -> dict:
        """GET an API path. `path` is relative, e.g. 'lego/parts/3001/'."""
        path = path.lstrip("/")
        qs = urllib.parse.urlencode(sorted(params.items())) if params else ""

        # R1: resolve the path against the prefix *before* checking it. A naive
        # startswith() on a concatenated string is bypassable - both
        # '../../sets/x/' and an absolute 'https://rebrickable.com/sets/x/'
        # survive concatenation but resolve to site HTML.
        base = urllib.parse.urljoin(API_PREFIX, path)
        if not base.startswith(API_PREFIX):
            raise ValueError(
                f"R1 violation: path {path!r} resolves to {base!r}, outside the "
                f"API. Only {API_PREFIX} may be fetched; site HTML never."
            )
        url = base + (f"?{qs}" if qs else "")

        cache_key = f"{path}?{qs}"
        if not refresh:
            with sqlite3.connect(self.db_path) as c:
                row = c.execute(
                    "SELECT body FROM api_cache WHERE cache_key=?", (cache_key,)
                ).fetchone()
            if row:
                return json.loads(row[0])

        gap = time.monotonic() - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"key {self.key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(10)
                return self.get(path, refresh=True, **params)
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"API {e.code} on {cache_key}: {detail}") from None
        finally:
            self._last_call = time.monotonic()

        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT OR REPLACE INTO api_cache VALUES (?,?,?)",
                (cache_key, body, datetime.now(timezone.utc).isoformat()),
            )
        return json.loads(body)

    def paged(self, path: str, page_size: int = 1000, **params):
        """Yield every result across all pages."""
        page = 1
        while True:
            data = self.get(path, page=page, page_size=page_size, **params)
            yield from data.get("results", [])
            if not data.get("next"):
                return
            page += 1

    # ------------------------------------------------------------ convenience

    def part(self, part_num: str) -> dict:
        return self.get(f"lego/parts/{urllib.parse.quote(part_num)}/")

    def part_external_ids(self, part_num: str) -> dict:
        """External IDs incl. LDraw - sharpens the geometry mapping (PRD 2.3)."""
        return self.part(part_num).get("external_ids", {})

    def set_parts(self, set_num: str):
        return self.paged(f"lego/sets/{urllib.parse.quote(set_num)}/parts/")

    def set_alternates(self, set_num: str):
        """MOC listing metadata for a set. R3: link out, never ingest designs."""
        return self.paged(f"lego/sets/{urllib.parse.quote(set_num)}/alternates/")

    def user_sets(self, user_token: str):
        """The user's owned sets - removes hand-entry friction (PRD 4.2 step 1)."""
        return self.paged(f"users/{user_token}/sets/")

    def user_allparts(self, user_token: str):
        return self.paged(f"users/{user_token}/allparts/")

    def user_build(self, user_token: str, set_num: str) -> dict:
        """Rebrickable's own buildability check - useful to validate L2 against."""
        return self.get(f"users/{user_token}/build/{urllib.parse.quote(set_num)}/")


def backfill_ldraw_ids(limit: int | None = None) -> None:
    """Use external_ids to map parts that !KEYWORDS matching missed (RISK-1).

    Only touches parts with no existing ldraw_map row, so it is resumable and
    never re-requests a part it already resolved.
    """
    api = RebrickableAPI()
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS ldraw_map (
                 part_num TEXT PRIMARY KEY, ldraw_id TEXT NOT NULL,
                 dat_path TEXT NOT NULL, match_method TEXT NOT NULL)"""
        )
        # Prioritise by how often the part actually occurs - highest value first.
        rows = c.execute(
            """SELECT ip.part_num, SUM(ip.quantity) q FROM inventory_parts ip
               LEFT JOIN ldraw_map m ON m.part_num = ip.part_num
               WHERE m.part_num IS NULL AND ip.is_spare = 0
               GROUP BY ip.part_num ORDER BY q DESC""" + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()

    print(f"{len(rows):,} unmapped parts to try, highest-occurrence first")
    added = 0
    for i, (part_num, qty) in enumerate(rows, 1):
        try:
            ext = api.part_external_ids(part_num)
        except RuntimeError as e:
            print(f"  skip {part_num}: {e}")
            continue
        ids = ext.get("LDraw") or []
        if ids:
            with sqlite3.connect(DB_PATH) as c:
                c.execute(
                    "INSERT OR REPLACE INTO ldraw_map VALUES (?,?,?,?)",
                    (part_num, str(ids[0]).lower(),
                     f"ldraw/parts/{str(ids[0]).lower()}.dat", "api_external_id"),
                )
            added += 1
        if i % 50 == 0:
            print(f"  {i:,}/{len(rows):,} checked, {added} mapped")
    print(f"done: {added} new geometry mappings")


if __name__ == "__main__":
    import sys

    try:
        api = RebrickableAPI()
    except MissingKey as e:
        print(e)
        raise SystemExit(2)

    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        backfill_ldraw_ids(limit=int(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        # Smoke test: a part everyone knows, the 2x4 brick.
        print(json.dumps(api.part("3001"), indent=2)[:600])
