"""The tools API runs as its own container with a flat module layout, so tests reach it by path.

Keeping it flat is what lets `uvicorn main:app` work unchanged inside the image, and putting the
directory on the path here is cheaper than packaging a service that only ever runs one way.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS_API = HERE.parent / "tools-api"
for directory in (TOOLS_API, HERE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
