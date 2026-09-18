# MOCForge HTTP API

The v1.1 service defined by [`CONTRACT.md`](../CONTRACT.md). It wraps the
generation engine (`inventory.py`, `ldraw.py`, `roles.py`, `generate.py` at the
repository root), the isometric renderer (`render.py`) and the download
packager (`export.py`), and reads nothing but the local data files.

## Run it

```
pip install -r api/requirements.txt
uvicorn api.main:app --port 8000
```

Startup takes a couple of seconds: the catalogue, the LDraw index and the
fitter are built once, not per request. Needs `data/mocforge.db`
(`python ingest.py build`) and `data/ldraw/complete.zip`.

```
curl -s localhost:8000/api/v1/health
curl -s 'localhost:8000/api/v1/sets/search?q=42151'
curl -s -X POST localhost:8000/api/v1/generate \
     -H 'content-type: application/json' -d '{"setNums":["42151-1"]}'
curl -s localhost:8000/api/v1/designs/<id>/ldr
curl -s -o pack.zip localhost:8000/api/v1/designs/<id>/package
curl -s -o step1.png localhost:8000/api/v1/designs/<id>/steps/1/image
```

Interactive docs are at `/docs`; the contract, not the generated schema, is the
specification.

## Run the tests

```
python -m pytest tests/api -q
```

45 tests, about 15 seconds, plus 46 in `tests/test_export.py` for the download
pack. They exercise the real catalogue and the real LDraw
library rather than mocks — the failure modes worth testing (a bare set number
resolving to the wrong version, an inventory with no geometry, an archetype
withholding) do not exist against a mock. If the data files are missing the
suite skips with an explanation instead of failing.

## Decisions a reviewer would otherwise question

**One engine, one thread.** `Catalogue` now accepts
`check_same_thread=False`, so the catalogue connection alone could be shared —
but it is not the binding constraint. `LDrawLibrary` reads from a single shared
`zipfile` handle, which concurrent reads corrupt, and it memoises parsed
geometry; `Fitter` and `Taxonomy` memoise into plain dicts. Every generation
and every inventory profile goes through all three. So the engine is built on,
and only ever used from, one `ThreadPoolExecutor` worker (`api/engine.py`,
`EngineHandle`), and handlers `await` it — serialised engine work, unblocked
event loop. A connection and a library per thread would duplicate the geometry
cache and the 64k-row part-name table per thread, which is exactly the memory
that makes repeat requests fast. `/health` and `/archetypes` answer from
startup snapshots, so a liveness probe never queues behind a ten-second build.
Making the two catalogue-only endpoints (`/sets/search`, `/sets/{setNum}`)
concurrent is now possible and is the first thing to do if search latency under
load ever matters.

**Confidence floor of 1.0, kept as a guard.** The contract tells the client
that `confidence` is always `1.0` and that only designs at `>= 1.0` are
returned. `Build.buildable` now enforces that in the engine — a `weak`
placement (validates, but precarious) blocks a build — so the floor here is
redundant rather than load-bearing. It is kept as the one line that would catch
a future scoring change quietly reintroducing sub-1.0 designs. A build held
back for a weak placement says so in its withheld reason.

**LDraw text comes from the engine, not from a copy of its header.**
`Fitter.ldr_text(build, title, name)` is the single place that knows the model
header, including the Rebrickable and LDraw attribution and the LEGO
non-affiliation notice, so the service calls it and keeps the string. The file
name is passed through because it lands in the model's `0 Name:` line: a
downloaded file names itself as it was downloaded.

**Two caches and a registry.** `/generate` is keyed on
`(sorted set numbers, sorted archetype names)` — the result is a pure function
of those, and an absent `archetypes` filter is normalised to the full list so
`null` and "all of them" share one entry. Inventories are cached separately
because `/generate` needs the same profile. `elapsedMs` is always the real
time the request took, so a cache hit reports ~0 ms rather than replaying the
original cost. Design ids are `sha256(archetype | variant | sorted setNums)`
truncated to 16 hex characters: stable for the triple, in every process and
after a restart, and opaque per contract rule 5. The LDraw text lives in an
in-process LRU registry sized well above the generate cache, and cached
responses re-register their designs on a hit, so a cached `ldrUrl` can never
404 while its response is still being served.

**Malformed, unknown, empty and geometry-less are four different answers.**
A set number that is not a set number is `400 invalid_request` with
`detail.malformed`; one that does not exist is `404 set_not_found` with
`detail.unresolved`; a set that exists but whose inventory is minifigs only
(e.g. `0011-2`) is a `200` with `pieces: 0`; and an inventory where no part has
LDraw geometry (e.g. `TRADINGCARD-6`) is a `200` with an empty `designs` and
one withheld reason per archetype saying exactly that, short-circuited before
the fitter runs so it costs milliseconds instead of seconds.

**Duplicates in `setNums` are summed, not de-duplicated.** Two copies of a set
is a real inventory. `setNums` comes back sorted and canonical, because an
inventory is a multiset and the order carries no meaning.

**Unknown fields are rejected everywhere, including the query string.**
Contract rule 6. Bodies get it from `extra="forbid"`; endpoints with no query
parameters declare an empty strict query model, since FastAPI otherwise
ignores unexpected ones. Every schema violation is answered `400
invalid_request` with the offending locations in `detail`, not FastAPI's
default 422.

**Attribution rides on headers.** PRD R4 requires Rebrickable and LDraw
attribution in API responses. A response field would have been compatible -
rule 6 governs requests, not responses - but the notice is identical on every
response and belongs to the service rather than to any one design, so it is a
header and the contract's shapes stay as declared. Every response therefore
carries `X-Data-Attribution`, the OpenAPI description repeats it, and the
notices are inside every generated `.ldr` file — which matters most, because a
downloaded model outlives the page that served it.

**One render pass per design, cached, feeding three endpoints.** The zip, the
finished-model image and every step image all come out of a single call to
`export.build_export`, cached by design id in `Engine._exports`. Whichever
endpoint is asked first pays the cost - about 1.2s for a 236-part building,
almost all of it drawing - and the other two are then free, so a client that
downloads the pack and then displays a step never draws the same model twice.
The cache is keyed by design id alone because the bytes cannot vary: an id is a
hash of (archetype, variant, setNums), and `export.py` puts no clock in the
package, so the same design always packages to the same bytes. Rendering the
finished-model image on its own was considered and rejected - it saves one
render out of nine and buys a second code path with its own temporary-directory
handling to get wrong.

**The `Build` is kept in the design registry, not re-fitted.** `DesignRecord`
now carries the `Build` its `.ldr` came from, because the images have to be
drawn from it. Re-deriving it from (archetype, variant, setNums) would run the
fitter again - seconds, for something already computed - and would give the
pictures their own chance to disagree with the steps the client was shown. A
build is one `PlacedPart` per piece and shares its geometry with the library's
cache, so a 236-part design costs tens of kilobytes, not megabytes.

**Four ways an export can fail, and four answers.** An unknown design id is
`404 design_not_found`; a step index outside a real design is `404
step_not_found`; a build the renderer cannot draw is `500 render_failed`; a
document or archive that will not assemble is `500 package_failed`. The last
two are separate codes because they fail in unrelated modules, and an
`export_failed` that covered both would make the server log the only way to
tell a geometry bug from a full disk.

**`/health` grew `features`.** Contract v1.1 makes the capability list the
sanctioned way for a client to discover the image and package endpoints: a
`404` on a design endpoint already means "no such design", so a client must be
able to ask before it calls. The web client offers no download unless the
service it is talking to lists `designPackage`, which is what keeps the mock
transport honest about not implementing it.

**`builderNotes` is a field, not a client-side reword.** Contract v1.2. The
engine is where warnings originate, so it is where they are translated:
`export.builder_notes` runs once per design in `Engine._record`, the result is
published on `Design.builderNotes`, and both the web page and the PDF render
that field verbatim. A table in Python and a second one in TypeScript would
drift, and the page and the printed instructions would then disagree about a
design's caveats. `warnings` keeps the raw diagnostics, which is what the API
is the right place for.

**No network. Ever.** PRD R1: the service imports `inventory`, `ldraw`,
`roles` and `generate` only, never `rebrickable_api`, and reads only
`data/mocforge.db` and `data/ldraw/complete.zip`.

**`ldrUrl` is relative** (`/api/v1/designs/<id>/ldr`). The service cannot know
the public origin it is reached through, and the client resolves it against
the base URL it already used.

## Known gaps

- `InventoryLot` is declared in `api/models.py` because the contract declares
  it, but no v1 endpoint returns one: `Inventory.lots` is a count. Left
  modelled pending a decision on a lots endpoint.
- Design ids are in-process, as the contract allows. After a restart an old id
  is a clean `404 design_not_found`; regenerating the same inventory recreates
  the same id.
- Generation is serialised process-wide. That is the right trade for v1 (see
  above), but it means one long build delays other engine-backed requests.
  Lifting it means making `LDrawLibrary` concurrency-safe, not adding threads
  here. Rendering a pack is on the same queue, and for the same reason.
- `Build.warnings` reaches neither the document nor the page. It is the
  engine's own diagnostic text - roles named by their match predicate,
  distances in LDU - so `export.builder_notes` translates the ones a builder
  can act on and drops the rest, including anything it does not recognise.
  The result is published as `Design.builderNotes` (contract v1.2) and both
  the web page and the PDF render that field verbatim. The raw text stays on
  `Design.warnings`, which is what the API is the right place for.
- No archetype declares an optional role yet, so `builderNotes` is empty for
  every design the engine currently produces: the translated branch is live
  and tested, but nothing exercises it in production until the roof role PRD
  2.9 plans exists. Making an existing role optional to try it out does not
  work - `_car_layout` indexes `a["chassis"]` directly, so the build raises
  and is withheld rather than warning.
- The web client shows the finished-model and step images only inside the
  downloaded PDF. `GET /designs/{id}/image` is implemented and tested but
  nothing on screen uses it yet, which is the obvious next thing: the design
  cards and the instruction viewer are still text-only.
