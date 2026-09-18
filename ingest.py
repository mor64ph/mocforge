"""MOCForge Phase 1 - catalogue ingest.

Builds data/mocforge.db from the Rebrickable bulk CSV dumps.

Compliance (see PRD 2.1):
  R1  Only cdn.rebrickable.com/media/downloads/ is ever fetched. Never site HTML.
  R2  Dump downloads are capped at once per 24h, enforced via the ingest_log table.
  R6  Dump headers are validated against EXPECTED; schema drift fails loudly.

Usage:
  python ingest.py download       # fetch dumps (respects the 24h cap)
  python ingest.py download --force
  python ingest.py build          # (re)build SQLite from data/raw
  python ingest.py verify         # run the Phase 1 exit gate
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "mocforge.db"

# R1: the only permitted network prefix. Asserted before every request.
CDN_PREFIX = "https://cdn.rebrickable.com/media/downloads/"
REFRESH_INTERVAL = timedelta(hours=24)  # R2

csv.field_size_limit(10_000_000)


def _int(v):
    """Empty strings become NULL; everything else is an int."""
    return int(v) if v not in ("", None) else None


def _bool(v):
    """Rebrickable writes True/False as text. Store 0/1 so SQL comparisons work."""
    return 1 if str(v).strip().lower() in ("true", "t", "1") else 0


def _text(v):
    return v if v != "" else None


# table -> (expected CSV header, DDL, primary key columns, column converters)
EXPECTED: dict[str, tuple] = {
    "themes": (
        ("id", "name", "parent_id"),
        "CREATE TABLE themes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER)",
        ("id",),
        {"id": _int, "parent_id": _int},
    ),
    "colors": (
        ("id", "name", "rgb", "is_trans", "num_parts", "num_sets", "y1", "y2"),
        """CREATE TABLE colors (id INTEGER PRIMARY KEY, name TEXT NOT NULL, rgb TEXT,
           is_trans INTEGER NOT NULL, num_parts INTEGER, num_sets INTEGER,
           y1 INTEGER, y2 INTEGER)""",
        ("id",),
        {"id": _int, "is_trans": _bool, "num_parts": _int, "num_sets": _int,
         "y1": _int, "y2": _int},
    ),
    "part_categories": (
        ("id", "name"),
        "CREATE TABLE part_categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
        ("id",),
        {"id": _int},
    ),
    "parts": (
        ("part_num", "name", "part_cat_id", "part_material"),
        """CREATE TABLE parts (part_num TEXT PRIMARY KEY, name TEXT NOT NULL,
           part_cat_id INTEGER, part_material TEXT)""",
        ("part_num",),
        {"part_cat_id": _int},
    ),
    "part_relationships": (
        ("rel_type", "child_part_num", "parent_part_num"),
        """CREATE TABLE part_relationships (rel_type TEXT NOT NULL,
           child_part_num TEXT NOT NULL, parent_part_num TEXT NOT NULL,
           PRIMARY KEY (rel_type, child_part_num, parent_part_num))""",
        ("rel_type", "child_part_num", "parent_part_num"),
        {},
    ),
    "elements": (
        ("element_id", "part_num", "color_id", "design_id"),
        """CREATE TABLE elements (element_id TEXT PRIMARY KEY, part_num TEXT NOT NULL,
           color_id INTEGER NOT NULL, design_id TEXT)""",
        ("element_id",),
        {"color_id": _int, "design_id": _text},
    ),
    "sets": (
        ("set_num", "name", "year", "theme_id", "num_parts", "img_url"),
        """CREATE TABLE sets (set_num TEXT PRIMARY KEY, name TEXT NOT NULL,
           year INTEGER, theme_id INTEGER, num_parts INTEGER, img_url TEXT)""",
        ("set_num",),
        {"year": _int, "theme_id": _int, "num_parts": _int},
    ),
    "minifigs": (
        ("fig_num", "name", "num_parts", "img_url"),
        """CREATE TABLE minifigs (fig_num TEXT PRIMARY KEY, name TEXT NOT NULL,
           num_parts INTEGER, img_url TEXT)""",
        ("fig_num",),
        {"num_parts": _int},
    ),
    "inventories": (
        ("id", "version", "set_num"),
        """CREATE TABLE inventories (id INTEGER PRIMARY KEY, version INTEGER NOT NULL,
           set_num TEXT NOT NULL)""",
        ("id",),
        {"id": _int, "version": _int},
    ),
    "inventory_parts": (
        ("inventory_id", "part_num", "color_id", "quantity", "is_spare", "img_url"),
        """CREATE TABLE inventory_parts (inventory_id INTEGER NOT NULL, part_num TEXT NOT NULL,
           color_id INTEGER NOT NULL, quantity INTEGER NOT NULL, is_spare INTEGER NOT NULL,
           img_url TEXT,
           PRIMARY KEY (inventory_id, part_num, color_id, is_spare))""",
        ("inventory_id", "part_num", "color_id", "is_spare"),
        {"inventory_id": _int, "color_id": _int, "quantity": _int, "is_spare": _bool},
    ),
    "inventory_sets": (
        ("inventory_id", "set_num", "quantity"),
        """CREATE TABLE inventory_sets (inventory_id INTEGER NOT NULL, set_num TEXT NOT NULL,
           quantity INTEGER NOT NULL, PRIMARY KEY (inventory_id, set_num))""",
        ("inventory_id", "set_num"),
        {"inventory_id": _int, "quantity": _int},
    ),
    "inventory_minifigs": (
        ("inventory_id", "fig_num", "quantity"),
        """CREATE TABLE inventory_minifigs (inventory_id INTEGER NOT NULL, fig_num TEXT NOT NULL,
           quantity INTEGER NOT NULL, PRIMARY KEY (inventory_id, fig_num))""",
        ("inventory_id", "fig_num"),
        {"inventory_id": _int, "quantity": _int},
    ),
}

INDEXES = [
    "CREATE INDEX idx_inv_parts_inv ON inventory_parts (inventory_id)",
    "CREATE INDEX idx_inv_parts_part ON inventory_parts (part_num, color_id)",
    "CREATE INDEX idx_inventories_set ON inventories (set_num, version)",
    "CREATE INDEX idx_sets_theme ON sets (theme_id)",
    "CREATE INDEX idx_sets_year ON sets (year)",
    "CREATE INDEX idx_rel_child ON part_relationships (child_part_num)",
    "CREATE INDEX idx_rel_parent ON part_relationships (parent_part_num)",
    "CREATE INDEX idx_elements_part ON elements (part_num, color_id)",
    "CREATE INDEX idx_inv_sets_inv ON inventory_sets (inventory_id)",
]

# Expected row counts, measured 2026-09-17. Used by verify() as a drift tripwire.
BASELINE_COUNTS = {
    "themes": 496, "colors": 275, "part_categories": 76, "parts": 64649,
    "part_relationships": 37416, "elements": 114283, "sets": 28356,
    "minifigs": 17225, "inventories": 47533, "inventory_parts": 1557673,
    "inventory_sets": 5212, "inventory_minifigs": 25824,
}


# ---------------------------------------------------------------- download (R1, R2)

def _log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ingest_log (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             action TEXT NOT NULL, ts TEXT NOT NULL, detail TEXT)"""
    )


def _last_download(conn: sqlite3.Connection) -> datetime | None:
    _log_table(conn)
    row = conn.execute(
        "SELECT ts FROM ingest_log WHERE action='download' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def download(force: bool = False) -> None:
    """Fetch the dumps. R2: refuses if the last fetch was under 24h ago."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        last = _last_download(conn)
        if last and not force:
            age = datetime.now(timezone.utc) - last
            if age < REFRESH_INTERVAL:
                wait = REFRESH_INTERVAL - age
                raise SystemExit(
                    f"R2: last download was {age} ago. Rebrickable permits one "
                    f"download per 24h. Retry in {wait}, or pass --force "
                    f"deliberately."
                )
        RAW.mkdir(parents=True, exist_ok=True)
        for table in EXPECTED:
            url = f"{CDN_PREFIX}{table}.csv.gz"
            # R1: hard guard. Never fetch anything but the CDN dump path.
            assert url.startswith(CDN_PREFIX), f"R1 violation: {url}"
            dest = RAW / f"{table}.csv"
            with urllib.request.urlopen(url, timeout=180) as resp:
                blob = gzip.decompress(resp.read())
            dest.write_bytes(blob)
            print(f"  downloaded {table:22s} {len(blob):>12,} bytes")
            time.sleep(1)  # be a polite client
        conn.execute(
            "INSERT INTO ingest_log (action, ts, detail) VALUES (?,?,?)",
            ("download", datetime.now(timezone.utc).isoformat(), f"{len(EXPECTED)} files"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- build

def _read(table: str):
    """Yield rows from a dump, validating the header first (R6)."""
    header, _ddl, _pk, conv = EXPECTED[table]
    path = RAW / f"{table}.csv"
    if not path.exists():
        raise SystemExit(f"missing dump: {path}. Run 'python ingest.py download' first.")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        actual = tuple(c.strip().lstrip("﻿") for c in next(reader))
        if actual != header:
            raise SystemExit(
                f"R6 schema drift in {table}.csv\n  expected: {header}\n  actual:   {actual}"
            )
        for row in reader:
            yield tuple(conv.get(col, str)(val) if col in conv else val
                        for col, val in zip(header, row))


def build() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    try:
        _log_table(conn)
        for table, (header, ddl, _pk, _conv) in EXPECTED.items():
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(ddl)
            placeholders = ",".join("?" * len(header))
            n = 0
            batch = []
            for row in _read(table):
                batch.append(row)
                if len(batch) >= 50_000:
                    conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({placeholders})", batch)
                    n += len(batch)
                    batch.clear()
            if batch:
                conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({placeholders})", batch)
                n += len(batch)
            print(f"  loaded {table:22s} {n:>10,} rows")
        for stmt in INDEXES:
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO ingest_log (action, ts, detail) VALUES (?,?,?)",
            ("build", datetime.now(timezone.utc).isoformat(), "ok"),
        )
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- verify (exit gate)

def fingerprint(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """Per-table (row count, content hash). Proves idempotency across rebuilds."""
    out = {}
    for table, (_h, _d, pk, _c) in EXPECTED.items():
        digest = hashlib.md5()
        count = 0
        order = ", ".join(pk)
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(repr(row).encode())
            count += 1
        out[table] = (count, digest.hexdigest())
    return out


def verify() -> int:
    conn = sqlite3.connect(DB_PATH)
    failures = []
    try:
        print("row counts vs baseline (PRD section 2):")
        fp = fingerprint(conn)
        for table, expected in BASELINE_COUNTS.items():
            got = fp[table][0]
            flag = "ok" if got == expected else "DRIFT"
            if got != expected:
                failures.append(f"{table}: expected {expected:,}, got {got:,}")
            print(f"  {table:22s} {got:>10,}  {flag}")

        # Gate: the 42151-1 fixture, cross-checked against LEGO's official count.
        row = conn.execute(
            """SELECT SUM(ip.quantity), COUNT(*)
               FROM inventory_parts ip
               JOIN inventories i ON i.id = ip.inventory_id
               WHERE i.set_num = '42151-1' AND i.version = 1 AND ip.is_spare = 0"""
        ).fetchone()
        pieces, lots = row
        print(f"\nfixture 42151-1: {pieces} pieces across {lots} lots (expect 905 / 150)")
        if (pieces, lots) != (905, 150):
            failures.append(f"42151-1 fixture: got {pieces}/{lots}, expected 905/150")

        # Referential sanity: every inventory must point at a real set.
        orphans = conn.execute(
            "SELECT COUNT(*) FROM inventories i LEFT JOIN sets s ON s.set_num=i.set_num "
            "WHERE s.set_num IS NULL"
        ).fetchone()[0]
        print(f"inventories with no matching set: {orphans}")

        # is_spare must be strictly 0/1 - guards the True/False text trap.
        bad = conn.execute(
            "SELECT COUNT(*) FROM inventory_parts WHERE is_spare NOT IN (0,1)"
        ).fetchone()[0]
        if bad:
            failures.append(f"is_spare has {bad} non-boolean rows")
        print(f"is_spare non-boolean rows: {bad}")
    finally:
        conn.close()

    print()
    if failures:
        print("GATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("GATE PASSED: Phase 1 data foundation verified.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "download":
        download(force="--force" in sys.argv)
    elif cmd == "build":
        build()
    elif cmd == "verify":
        sys.exit(verify())
    elif cmd == "fingerprint":
        c = sqlite3.connect(DB_PATH)
        for t, (n, h) in fingerprint(c).items():
            print(f"{t:22s} {n:>10,}  {h}")
        c.close()
    else:
        raise SystemExit(__doc__)
