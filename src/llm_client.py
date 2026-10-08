"""Groq LLM client wrapper.

The copilot uses Groq for *interpretation and explanation only* - never for
deterministic policy decisions (those live in ``policy_engine``). The wrapper is
fully configurable through environment variables and contains no hardcoded keys.

Environment variables
----------------------
- ``GROQ_API_KEY``   : required to make live calls (never hardcoded / never logged)
- ``GROQ_MODEL`` or ``MODEL_NAME`` : model id (default: ``llama-3.3-70b-versatile``)
- ``GROQ_BASE_URL``  : optional custom endpoint

Graceful degradation
---------------------
If the SDK is missing or no API key is configured, ``is_available()`` returns
False and callers fall back to a deterministic, policy-grounded summary. This
keeps the evaluation harness and tests runnable in a clean/offline environment.
"""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqClient:
    """Thin, defensive wrapper around the Groq chat completions API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY")
        self.model = os.getenv("GROQ_MODEL") or os.getenv("MODEL_NAME") or DEFAULT_MODEL
        self.base_url = os.getenv("GROQ_BASE_URL")
        self._client: Any = None
        self._init_error: str | None = None

    def is_available(self) -> bool:
        """True only if the SDK imports and an API key is configured."""
        if not self.api_key:
            return False
        if self._client is not None:
            return True
        try:
            from groq import Groq  # type: ignore

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Groq(**kwargs)
            return True
        except Exception as exc:  # SDK missing or init failure
            self._init_error = str(exc)
            return False

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 900,
    ) -> dict[str, Any]:
        """Request a JSON object completion.

        Returns a dict with ``ok`` plus either ``data`` (parsed JSON) or
        ``error``. Never raises to the caller - the orchestrator must stay
        robust when the LLM is unavailable or returns malformed output.
        """
        if not self.is_available():
            return {
                "ok": False,
                "error": self._init_error or "Groq API key not configured.",
            }
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            return {"ok": True, "data": data, "model": self.model}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"LLM returned non-JSON content: {exc}"}
        except Exception as exc:  # network / auth / rate-limit
            return {"ok": False, "error": f"Groq call failed: {exc}"}
