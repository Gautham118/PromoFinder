"""Minimal reader for PromoFinder's deliberately flat configuration file."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


# The rubric lives next to the package, so stages work from any working directory.
RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubric.md"


class AppConfig(BaseModel):
    provider: str = "mock"
    model: str = "mock-promofinder"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key_env: str = "GROQ_API_KEY"
    cache_dir: str = ".cache"
    timeout_s: float = 60
    max_retries: int = Field(default=3, ge=0)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Read the project-owned flat YAML subset without adding a YAML dependency."""
    values: dict[str, str] = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{number}: expected key: value")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise ValueError(f"{path}:{number}: expected non-empty key and value")
        values[key] = value.strip("\"'")
    return AppConfig.model_validate(values)


def load_dotenv(path: str | Path = ".env") -> None:
    """Tiny .env reader (KEY=VALUE lines). Never overrides variables that are already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value
