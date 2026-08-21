from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def vectors() -> dict[str, Any]:
    return json.loads(
        (ROOT / "vectors" / "phase2-v1.json").read_text(encoding="utf-8")
    )


def unhex(value: str) -> bytes:
    return bytes.fromhex(value)
