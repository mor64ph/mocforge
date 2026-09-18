# MOCForge

Generates buildable LEGO MOC designs from the sets you already own.

Existing MOC sites host human-designed models and tell you which ones you can
*almost* build — typically "you're missing 47 parts." MOCForge inverts that: it
**generates** the design to fit your inventory, so every result is buildable
with zero purchases.

Point it at one or more set numbers and it returns complete designs with
step-by-step instructions, an isometric render of every step, an LDraw file you
can open in any LDraw viewer, and a printable instruction PDF.

## How it works

```
Rebrickable bulk dumps -> SQLite catalogue -> LDraw geometry -> role taxonomy
  -> archetype templates -> collision + stability validation -> LDraw / PNG / PDF
```

Generation is **constraint solving, not generative ML**. Every part is an
axis-aligned box whose exact position, size and colour are known, so placement
is a geometry problem: parts are assigned to functional roles, tiled into a
template, then checked for collisions, support and connectivity. There is no
model, no training data, and no third-party MOC is ever ingested.

Nothing partial is ever offered. A design that fails validation is withheld
with a stated reason rather than shown as a flawed model.

**Current archetypes:** 7 templates / 13 variants — studded car, axle car,
Technic chassis, Technic crib, Technic ladder frame, brick building (cottage /
hall / tower) and stepped plate sculpture (ziggurat / terrace / mosaic).

## Status

Honest accounting of what is and is not finished:

| Area | State |
|---|---|
| Catalogue ingest, inventory algebra, LDraw geometry | Complete and gated |
| Generation engine | Complete bar one gate (below) |
| Web app | Built; end-to-end integration gate not yet executed |
| ML layer | Not started |

- Geometry coverage is **96.3% brick-weighted**; all 500 largest sets clear the
  80% threshold the project set as its stop-or-replan gate.
- **103 Python tests and 49 web tests pass.** The generator's own gate audits
  **156 designs across 31 inventories** for parts-used exceeding parts-owned,
  and finds none.
- The generation engine's remaining gate is a **physical build check**: three
  generated models assembled by hand. Collision maths cannot substitute for it,
  so it is deliberately still open.

## Setup

Developed and tested on Python 3.14. The web client requires Node >= 24, as
declared in `web/package.json` (Next 16 / React 19).

```bash
pip install -r api/requirements.txt
```

Two data sources are needed and neither is redistributed here:

1. **LDraw parts library** — download `complete.zip` from
   <https://library.ldraw.org/> and place it at `data/ldraw/complete.zip`.
   Nothing is extracted; parts are read straight out of the zip.
2. **Rebrickable catalogue** — build it from the official bulk dumps:

```bash
python ingest.py download   # fetch dumps (capped at once per 24h)
python ingest.py build      # build data/mocforge.db
python ingest.py verify     # run the exit gate
```

This produces a ~231 MB SQLite database. A Rebrickable API key at
`data/api/key.txt` is needed only for the live JSON API helper, not for
generation.

## Running it

Two terminals:

```bash
# API
python -m uvicorn api.main:app --port 8140
```

```bash
# Web client
cd web
NEXT_PUBLIC_MOCFORGE_API=http://127.0.0.1:8140 npm run dev
```

Open <http://localhost:3000>. Browse via `localhost` rather than `127.0.0.1` —
that is what the API's CORS allowlist expects.

`NEXT_PUBLIC_MOCFORGE_API` matters: unset, the client runs against an
in-process mock instead of your API. Allow the API a few seconds on startup to
load the catalogue and index the parts library once.

### Command line

```bash
python generate.py list                    # all archetypes and their roles
python generate.py suggest 42151-1         # every design this inventory supports
python generate.py suggest 10696-1 42151-1 # combined inventory
python generate.py build sculpture 10696-1 # one design, written as .ldr
python generate.py selftest                # the consistency gate
```

## Data sourcing

By design, this project **never scrapes**. It reads only Rebrickable's public
CDN bulk dumps and, where a live lookup is needed, the documented
`/api/v3/` JSON API. No request is ever made to a Rebrickable HTML page, dump
downloads are rate-limited to once per 24 hours in code, and no third-party MOC
design is ingested, stored or used as training data.

## Attribution

Catalogue data from [Rebrickable](https://rebrickable.com/). Part geometry from
the [LDraw Parts Library](https://library.ldraw.org/), licensed CC BY 2.0.

LEGO is a trademark of the LEGO Group, which does not sponsor, authorise or
endorse this project. This is an unofficial fan project and is not affiliated
with the LEGO Group in any way.
