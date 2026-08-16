"""Ollama HTTP provider — local only, no cloud APIs."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from bot.research.llm.provider import ProviderError, ProviderHealth, ResearchLLMProvider

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OllamaProvider(ResearchLLMProvider):
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3:4b-instruct",
        timeout_seconds: float = 120.0,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        max_retries: int = 2,
        max_response_bytes: int = 512_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self._health_cache: ProviderHealth | None = None
        self._health_cache_at: float = 0.0

    def health(self) -> ProviderHealth:
        now = time.time()
        if self._health_cache and now - self._health_cache_at < 30.0:
            return self._health_cache
        try:
            tags = self._get_json("/api/tags", timeout=min(5.0, self.timeout_seconds))
            models = [m.get("name") for m in (tags.get("models") or []) if isinstance(m, dict)]
            # exact or prefix match (ollama may append :latest)
            found = any(
                str(n) == self.model or str(n).startswith(self.model.split(":")[0] + ":")
                for n in models
                if n
            )
            # also accept exact configured tag present
            found = found or self.model in models
            if not found and models:
                # check substring for qwen3:4b-instruct variants
                found = any(self.model in str(n) or str(n).startswith(self.model) for n in models)
            if not models:
                status = "UNAVAILABLE"
                detail = "ollama reachable but no models listed"
                available = False
            elif not found:
                status = "MODEL_UNAVAILABLE"
                detail = f"model {self.model} not installed; available={models[:8]}"
                available = False
            else:
                status = "AVAILABLE"
                detail = f"models={len(models)}"
                available = True
            health = ProviderHealth(
                available=available,
                status=status,
                provider="ollama",
                model=self.model,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001
            health = ProviderHealth(
                available=False,
                status="UNAVAILABLE",
                provider="ollama",
                model=self.model,
                detail=str(exc)[:300],
            )
        self._health_cache = health
        self._health_cache_at = now
        return health

    def generate_structured(
        self,
        *,
        system_prompt: str,
        context: dict[str, Any],
        schema_model: type[T],
    ) -> T:
        h = self.health()
        if not h.available:
            raise ProviderError(f"LLM status={h.status}: {h.detail}")

        user_payload = {
            "context": context,
            "response_schema": schema_model.model_json_schema(),
            "instruction": (
                "Return ONLY valid JSON matching the schema. "
                "No markdown. No executable code. No Python. Extra fields forbidden."
            ),
        }
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, sort_keys=True)},
            ],
        }
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self._post_json("/api/chat", body, timeout=self.timeout_seconds)
                content = ((raw.get("message") or {}).get("content")) or raw.get("response") or ""
                if isinstance(content, dict):
                    payload = content
                else:
                    text = str(content).strip()
                    if len(text.encode("utf-8")) > self.max_response_bytes:
                        raise ProviderError("response exceeds size limit")
                    payload = json.loads(text)
                return schema_model.model_validate(payload)
            except (ValidationError, json.JSONDecodeError, ProviderError, urllib.error.URLError) as exc:
                last_err = exc
                logger.warning("OLLAMA_GENERATE_FAILED attempt=%s error=%s", attempt, exc)
                time.sleep(0.25 * (attempt + 1))
        raise ProviderError(f"structured generation failed: {last_err}")

    def _get_json(self, path: str, *, timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(self.base_url + path, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = resp.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise ProviderError("response too large")
            return json.loads(data.decode("utf-8"))

    def _post_json(self, path: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = resp.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise ProviderError("response too large")
            return json.loads(data.decode("utf-8"))


def build_provider_from_settings(settings: Any) -> ResearchLLMProvider:
    return OllamaProvider(
        base_url=str(getattr(settings, "research_llm_base_url", "http://127.0.0.1:11434")),
        model=str(getattr(settings, "research_llm_model", "qwen3:4b-instruct")),
        timeout_seconds=float(getattr(settings, "research_llm_timeout_seconds", 120.0)),
        max_tokens=int(getattr(settings, "research_llm_max_tokens", 4096)),
        temperature=float(getattr(settings, "research_llm_temperature", 0.2)),
    )
