"""Provider abstraction. The OpenAI-compatible implementation supports Groq and similar APIs."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import load_dotenv
from .cache import DiskCache

T = TypeVar("T", bound=BaseModel)
SCHEMA_VERSION = "stage2-4-v1"
RETRY_STATUSES = {408, 429}          # plus every 5xx; other 4xx (bad key, bad request) fail at once


class LLMError(RuntimeError):
    """A provider or structured-output failure that callers can report without repair."""


class LLMClient(Protocol):
    def complete_json(self, *, system: str, user: str, response_model: type[T], cache_bypass: bool = False) -> T: ...


class MockClient:
    """Offline client for deterministic tests and code-only baselines."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, response_model: type[T], cache_bypass: bool = False) -> T:
        self.requests.append({"system": system, "user": user, "response_model": response_model.__name__})
        payload = self.responses.pop(0) if self.responses else {}
        try:
            return response_model.model_validate(payload)
        except ValidationError as e:
            raise LLMError(f"mock response does not match {response_model.__name__}: {e}") from e


class OpenAICompatibleClient:
    def __init__(self, *, model: str, base_url: str, api_key_env: str, cache_dir: str | Path = ".cache",
                 timeout_s: float = 60, max_retries: int = 3, temperature: float = 0, seed: int | None = 2026,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.model, self.base_url, self.api_key_env = model, base_url.rstrip("/"), api_key_env
        self.cache = DiskCache(cache_dir)
        self.timeout_s, self.max_retries, self.temperature, self.seed = timeout_s, max_retries, temperature, seed
        self.sleep = sleep
        # Running totals so real token use can be measured (CLAUDE.md section 5). Tokens are as reported by the API.
        self.usage = {"calls": 0, "cache_hits": 0, "repairs": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete_json(self, *, system: str, user: str, response_model: type[T], cache_bypass: bool = False) -> T:
        request = {
            "provider": "openai-compatible", "model": self.model, "base_url": self.base_url,
            "system": system, "user": user, "schema": response_model.model_json_schema(),
            "temperature": self.temperature, "seed": self.seed, "schema_version": SCHEMA_VERSION,
        }
        key = self.cache.key(request)
        cached = None if cache_bypass else self.cache.get(key)
        if cached is not None:
            self.usage["cache_hits"] += 1
            return self._validate(cached, response_model, "cached")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = self._chat(messages)
        try:
            payload = _json_object(text)
            result = self._validate(payload, response_model, "provider")
        except (ValueError, LLMError) as first:
            # CLAUDE.md 6c: structured JSON validated with Pydantic, with ONE repair retry.
            self.usage["repairs"] += 1
            repair = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"Your previous reply was not valid. Problem: {str(first)[:500]}\n"
                                            "Return ONLY the corrected JSON object in the required format, with no other text."}]
            text2 = self._chat(repair)
            try:
                payload = _json_object(text2)
                result = self._validate(payload, response_model, "provider")
            except (ValueError, LLMError) as second:
                raise LLMError(f"invalid output after one repair retry: {str(second)[:500]}") from second
        if not cache_bypass:
            self.cache.put(key, payload)
        return result

    # -- transport ---------------------------------------------------------------------------------
    def _send(self, url: str, headers: dict[str, str], raw: bytes) -> tuple[int, dict[str, str], str]:
        """One HTTP POST. Returns (status, lower-cased headers, body). Tests replace this method."""
        req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", errors="replace")

    def _chat(self, messages: list[dict[str, str]]) -> str:
        load_dotenv(".env")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise LLMError(f"missing API key environment variable: {self.api_key_env}")
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature,
                                "response_format": {"type": "json_object"}}
        if self.seed is not None:
            body["seed"] = self.seed
        raw = json.dumps(body).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                status, rh, text = self._send(url, headers, raw)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                status, rh, text = 599, {}, f"network error: {e}"
            if status == 200:
                return self._content(text)
            last = f"HTTP {status}: {text[:300]}"
            if status not in RETRY_STATUSES and status < 500:
                raise LLMError(last)                      # bad key / bad request: retrying cannot help
            if attempt < self.max_retries:
                self.sleep(_wait_seconds(rh, attempt))
        raise LLMError(f"LLM request failed after {self.max_retries + 1} attempts; last: {last}")

    def _content(self, body: str) -> str:
        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {e}; body starts: {body[:200]}") from e
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not content:
            raise LLMError("the model returned an empty message")
        usage = data.get("usage") or {}
        self.usage["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens"):
            if isinstance(usage.get(k), int):
                self.usage[k] += usage[k]
        return str(content)

    @staticmethod
    def _validate(payload: dict[str, Any], response_model: type[T], source: str) -> T:
        try:
            return response_model.model_validate(payload)
        except ValidationError as e:
            raise LLMError(f"{source} response does not match {response_model.__name__}: {e}") from e


def _wait_seconds(headers: dict[str, str], attempt: int) -> float:
    """Honour Retry-After when the provider sends it (free tiers rate-limit hard), else exponential backoff."""
    try:
        if "retry-after" in headers:
            return min(float(headers["retry-after"]), 120.0)
    except ValueError:
        pass
    return min(2.0 ** attempt, 30.0)


def _json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b <= a:
            raise ValueError("reply is not valid JSON")
        value = json.loads(text[a:b + 1])
    if not isinstance(value, dict):
        raise ValueError("provider returned JSON that is not an object")
    return value


def create_client(provider: str, *, model: str, base_url: str, api_key_env: str, cache_dir: str | Path = ".cache",
                  timeout_s: float = 60, max_retries: int = 3) -> LLMClient:
    if provider == "mock":
        return MockClient()
    if provider in {"openai-compatible", "groq"}:
        return OpenAICompatibleClient(model=model, base_url=base_url, api_key_env=api_key_env, cache_dir=cache_dir,
                                      timeout_s=timeout_s, max_retries=max_retries)
    raise ValueError(f"unsupported provider: {provider}")
