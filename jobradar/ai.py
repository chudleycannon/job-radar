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
    data: dict[str, Any] = {}
    for attempt in range(2):
        data = _anthropic_message(
            prompt, api_key=api_key, model=model, base_url=base_url,
            max_tokens=max_tokens, timeout=timeout)
        text = _content_text(data).strip()
        if text:
            return text
        # DeepSeek documents occasional empty content in JSON-shaped replies.
        # Retrying once is cheap beside dropping a whole ranking batch, and it
        # is still bounded: a persistent blank answer becomes a real error.
        if attempt == 0:
            continue
    raise AIError(_blank_text_error(data))


def _anthropic_message(prompt: str, *, api_key: str, model: str,
                       base_url: str = "", max_tokens: int = 4096,
                       timeout: int | None = None) -> dict[str, Any]:
    try:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": prompt}],
        }
        if _is_deepseek(base_url):
            # DeepSeek's Anthropic-compatible endpoint defaults to thinking
            # mode. Ranking asks for a short JSON array, so hidden reasoning
            # can spend the response budget before any visible text is
            # emitted. Disable it for these one-shot structured calls.
            body["reasoning"] = {"effort": "none"}
        r = requests.post(
            _anthropic_url(base_url),
            headers={
                "Content-Type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
                "x-api-key": api_key,
            },
            json=body,
            timeout=timeout or 120,
        )
    except requests.RequestException as exc:
        raise AIError(f"AI request failed: {exc}") from exc
    if r.status_code in (402, 429):
        raise AILimitReached(_error_text(r))
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    try:
        data = r.json()
    except json.JSONDecodeError as exc:
        raise AIError("AI response was not JSON") from exc
    if not isinstance(data, dict):
        raise AIError("AI response JSON was not an object")
    return data


def _content_text(data: dict[str, Any]) -> str:
    content = data.get("content", [])
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for part in content:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict) and part.get("type") in (None, "text"):
            out.append(str(part.get("text") or ""))
    return "".join(out)


def _blank_text_error(data: dict[str, Any]) -> str:
    stop = data.get("stop_reason") or data.get("stop_sequence") or ""
    content = data.get("content", [])
    if isinstance(content, list):
        kinds = [str(p.get("type") or "object") if isinstance(p, dict)
                 else type(p).__name__ for p in content]
        shape = ", ".join(kinds) if kinds else "empty content array"
    else:
        shape = type(content).__name__
    detail = f"; stop_reason={stop}" if stop else ""
    return f"AI response contained no text ({shape}{detail})"


def _anthropic_url(base_url: str = "") -> str:
    base = (base_url or ANTHROPIC_BASE_URL).strip().rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    return base + "/v1/messages"


def _is_deepseek(base_url: str = "") -> bool:
    return "api.deepseek.com" in (base_url or "").lower()


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
