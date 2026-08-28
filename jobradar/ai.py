"""Direct AI provider calls for ranking and document generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AIError(RuntimeError):
    """The provider rejected or failed a request."""


class AILimitReached(AIError):
    """The provider says the account is out of credit or rate limited."""


@dataclass(frozen=True)
class AISettings:
    provider: str
    model: str
    anthropic_api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096

    @property
    def direct(self) -> bool:
        return self.provider == "anthropic" and bool(self.anthropic_api_key)


def settings_from_config(cfg) -> AISettings:
    return AISettings(
        provider=getattr(cfg, "ai_provider", "claude_cli") or "claude_cli",
        model=getattr(cfg, "ai_model", "claude-sonnet-5") or "claude-sonnet-5",
        anthropic_api_key=getattr(cfg, "anthropic_api_key", "") or "",
        base_url=getattr(cfg, "ai_base_url", "") or "",
        max_tokens=int(getattr(cfg, "ai_max_tokens", 4096) or 4096),
    )


def configured(cfg) -> bool:
    return settings_from_config(cfg).direct


def complete(prompt: str, cfg, *, timeout: int | None = None,
             max_tokens: int | None = None) -> str:
    """Return assistant text from the configured direct provider."""
    settings = settings_from_config(cfg)
    if not settings.direct:
        raise AIError("no direct AI provider is configured")
    return anthropic_complete(
        prompt,
        api_key=settings.anthropic_api_key,
        model=settings.model,
        base_url=settings.base_url,
        max_tokens=max_tokens or settings.max_tokens,
        timeout=timeout,
    )


def anthropic_complete(prompt: str, *, api_key: str, model: str,
                       base_url: str = "",
                       max_tokens: int = 4096,
                       timeout: int | None = None) -> str:
    """Call Anthropic's Messages API and return the concatenated text blocks.

    Anthropic documents the Messages endpoint as `POST /v1/messages` with an
    `x-api-key`, `anthropic-version`, `model`, `max_tokens` and a user message.
    """
    if not api_key:
        raise AIError("ANTHROPIC_API_KEY is empty")
    try:
        r = requests.post(
            _anthropic_url(base_url),
            headers={
                "Content-Type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
                "x-api-key": api_key,
            },
            json={
                "model": model,
                "max_tokens": int(max_tokens),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout or 120,
        )
    except requests.RequestException as exc:
        raise AIError(f"AI request failed: {exc}") from exc
    if r.status_code in (402, 429):
        raise AILimitReached(_error_text(r))
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    try:
        data: dict[str, Any] = r.json()
    except json.JSONDecodeError as exc:
        raise AIError("AI response was not JSON") from exc
    text = "".join(
        str(part.get("text") or "")
        for part in data.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if not text:
        raise AIError("AI response contained no text")
    return text


def _anthropic_url(base_url: str = "") -> str:
    base = (base_url or ANTHROPIC_BASE_URL).strip().rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    return base + "/v1/messages"


def _error_text(r: requests.Response) -> str:
    try:
        data = r.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type")
            if msg:
                return str(msg)[:400]
    except Exception:
        pass
    return (r.text or f"HTTP {r.status_code}")[:400]
