"""Small, provider-agnostic disk cache for structured LLM responses."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def key(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
