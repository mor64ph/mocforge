"""MOCForge HTTP API service (see CONTRACT.md for the normative spec).

The generation engine lives in top-level modules at the repository root
(`inventory.py`, `ldraw.py`, `roles.py`, `generate.py`) rather than in a
package, and those files are owned by another workstream, so they cannot be
moved under `api/`. Importing them therefore requires the repository root on
`sys.path`; doing it here means `uvicorn api.main:app` works from any working
directory instead of only from the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = ["REPO_ROOT"]
