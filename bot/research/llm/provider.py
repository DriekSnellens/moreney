"""Research LLM provider abstraction — local only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ProviderHealth:
    available: bool
    status: str  # AVAILABLE | UNAVAILABLE | MODEL_UNAVAILABLE
    provider: str
    model: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "detail": self.detail,
        }


class ResearchLLMProvider(ABC):
    """Isolated provider interface — no shell, Redis, or trading access."""

    @abstractmethod
    def health(self) -> ProviderHealth:
        ...

    @abstractmethod
    def generate_structured(
        self,
        *,
        system_prompt: str,
        context: dict[str, Any],
        schema_model: type[T],
    ) -> T:
        ...


class ProviderError(RuntimeError):
    """Provider-level failure (timeout, unavailable, malformed)."""


class FakeResearchLLMProvider(ResearchLLMProvider):
    """Deterministic fake for unit tests — never calls network."""

    def __init__(
        self,
        *,
        responses: list[dict[str, Any]] | None = None,
        health_status: str = "AVAILABLE",
        model: str = "fake-qwen3",
    ) -> None:
        self._responses = list(responses or [])
        self._health_status = health_status
        self._model = model
        self.calls: list[dict[str, Any]] = []

    def health(self) -> ProviderHealth:
        ok = self._health_status == "AVAILABLE"
        return ProviderHealth(
            available=ok,
            status=self._health_status,
            provider="fake",
            model=self._model,
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        context: dict[str, Any],
        schema_model: type[T],
    ) -> T:
        self.calls.append(
            {
                "system_prompt_len": len(system_prompt),
                "context_keys": sorted(context.keys()),
                "schema": schema_model.__name__,
            }
        )
        if self._health_status != "AVAILABLE":
            raise ProviderError(f"provider status={self._health_status}")
        if not self._responses:
            raise ProviderError("no fake responses configured")
        payload = self._responses.pop(0)
        try:
            return schema_model.model_validate(payload)
        except ValidationError as exc:
            raise ProviderError(f"malformed structured output: {exc}") from exc
