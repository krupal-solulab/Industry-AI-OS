"""Make the orchestrator package importable when running the suite.

Only `ai_os_shared` is installed editable in the workspace venv; the service packages
live under `services/<name>/src` and aren't on `sys.path` by default. This mirrors the
self-contained path resolution used by `packages/shared/tests/test_industry.py`, so the
orchestrator tests run without needing a PYTHONPATH export.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
