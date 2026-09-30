"""NOVA backend — Hugging Face InferenceClient wrapper (chat + JSON).

Design goals:

* never crash a task — every failure degrades to the deterministic fallback
  planner for the rest of the process,
* expose ``llm.mode()`` / ``llm.model_name`` for ``/api/health``,
* optionally run a local ``transformers`` model when ``NOVA_LOCAL_MODEL`` is set.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Iterable, Optional

from ..config import settings

log = logging.getLogger("nova.llm")


class LLMUnavailable(RuntimeError):
    """Raised when the model cannot be used (callers fall back to rules)."""


_FENCE_RE = re.compile(r"```(?:json|javascript|js|python|py)?\s*(.*?)```", re.S | re.I)


def _extract_balanced_json(text: str) -> Optional[dict]:
    """Strip code fences, locate the first balanced ``{...}`` and parse it."""
    if not text:
        return None
    raw = text.strip()

    candidates: list[str] = []
    fences = _FENCE_RE.findall(raw)
    candidates.extend(fences)
    candidates.append(raw)

    for chunk in candidates:
        chunk = chunk.strip()
        parsed = _try_parse(chunk)
        if parsed is not None:
            return parsed
        start = chunk.find("{")
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(chunk)):
                ch = chunk[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        parsed = _try_parse(chunk[start : i + 1])
                        if parsed is not None:
                            return parsed
                        break
            if start + 1 >= len(chunk):
                break
            start = chunk.find("{", start + 1)
    return None


def _try_parse(candidate: str) -> Optional[dict]:
    try:
        obj = json.loads(candidate)
    except Exception:
        return None
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0]
    return None


class LLMClient:
    """Thin wrapper around ``huggingface_hub.InferenceClient``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client = None
        self._local = None
        self._degraded = False          # permanently switch to rule based mode
        self._last_error: str = ""
        self._token_present = bool(settings.hf_token)
        self._local_enabled = bool(settings.local_model)
        self._local_import_error = ""

    # -- mode ------------------------------------------------------------
    def mode(self) -> str:
        """``"huggingface" | "local" | "fallback"``."""
        if self._degraded:
            return "fallback"
        if self._local_enabled:
            return "local" if self._local_ready() else "fallback"
        if self._token_present:
            return "huggingface"
        return "fallback"

    @property
    def model_name(self) -> str:
        if self._local_enabled and self.mode() == "local":
            return settings.local_model
        if self._token_present:
            return settings.hf_model
        return "nova-rules-v1 (offline fallback)"

    @property
    def available(self) -> bool:
        return self.mode() in {"huggingface", "local"}

    @property
    def last_error(self) -> str:
        return self._last_error

    def voice_available(self) -> bool:
        return self._token_present and not self._degraded

    # -- degradation -----------------------------------------------------
    def _degrade(self, exc: BaseException) -> None:
        with self._lock:
            self._degraded = True
        self._last_error = f"{type(exc).__name__}: {exc}"
        log.warning("LLM unavailable, switching permanently to fallback mode: %s", self._last_error)

    # -- clients ---------------------------------------------------------
    def _hf(self):
        if self._client is None:
            from huggingface_hub import InferenceClient  # local import: optional dependency

            self._client = InferenceClient(
                model=settings.hf_model,
                token=settings.hf_token or None,
                timeout=60,
            )
        return self._client

    def _local_ready(self) -> bool:
        if self._local is not None:
            return True
        if not self._local_enabled:
            return False
        try:
            import torch  # noqa: F401
            from transformers import AutoTokenizer, pipeline  # noqa: F401
        except Exception as exc:  # pragma: no cover - optional heavy deps
            self._local_import_error = str(exc)
            return False
        try:  # pragma: no cover - requires GPU/heavy model download
            pipe = pipeline(
                "text-generation",
                model=settings.local_model,
                device_map="auto",
                torch_dtype="auto",
            )
            self._local = pipe
            return True
        except Exception as exc:  # pragma: no cover
            self._local_import_error = str(exc)
            log.warning("local model failed to load: %s", exc)
            return False

    # -- public API ------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 700,
    ) -> str:
        """Return the assistant text for ``messages``. Raises on failure."""
        if not self.available:
            raise LLMUnavailable(self._last_error or "no model configured")

        if self.mode() == "local":
            try:
                return self._chat_local(messages, max_tokens)
            except Exception as exc:
                self._degrade(exc)
                raise LLMUnavailable(str(exc)) from exc

        try:
            client = self._hf()
            out = client.chat_completion(
                messages=messages,
                model=settings.hf_model,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            return _completion_text(out)
        except Exception as exc:
            self._degrade(exc)
            raise LLMUnavailable(str(exc)) from exc

    def chat_json(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 900,
    ) -> dict:
        """Chat expecting a JSON object answer (fences stripped + 1 repair retry)."""
        raw = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        parsed = _extract_balanced_json(raw)
        if parsed is not None:
            return parsed

        # one repair retry with an explicit instruction
        repair = list(messages) + [
            {"role": "assistant", "content": raw or ""},
            {
                "role": "user",
                "content": (
                    "That was not valid JSON. Reply again with ONLY a single JSON object, "
                    "no markdown, no commentary."
                ),
            },
        ]
        raw2 = self.chat(repair, temperature=0.0, max_tokens=max_tokens)
        parsed = _extract_balanced_json(raw2)
        if parsed is not None:
            return parsed
        raise LLMUnavailable("model did not return parseable JSON")

    def transcribe(self, audio: bytes) -> str:
        """Automatic speech recognition (``openai/whisper-small``)."""
        if not self._token_present:
            raise LLMUnavailable("HF_TOKEN not configured")
        try:
            client = self._hf()
            out = client.automatic_speech_recognition(
                audio, model="openai/whisper-small"
            )
        except Exception as exc:
            self._degrade(exc)
            raise LLMUnavailable(str(exc)) from exc
        if isinstance(out, dict):
            return str(out.get("text", "")).strip()
        return str(getattr(out, "text", out)).strip()

    def _chat_local(self, messages: list[dict], max_tokens: int) -> str:  # pragma: no cover
        if not self._local_ready():
            raise LLMUnavailable(self._local_import_error or "local model unavailable")
        out = self._local(
            list(messages),
            max_new_tokens=int(max_tokens),
            do_sample=False,
            return_full_text=False,
        )
        text = out[0].get("generated_text", "") if out else ""
        if isinstance(text, list):  # some pipelines return message lists
            text = text[-1].get("content", "") if text else ""
        return str(text).strip()


def _completion_text(out: Any) -> str:
    """Extract assistant content from a ``ChatCompletionOutput`` (or dict)."""
    if isinstance(out, dict):
        choices = out.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                return "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            return str(content or "")
        return ""
    choices = getattr(out, "choices", None) or []
    if not choices:
        return ""
    first = choices[0]
    msg = getattr(first, "message", first)
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(getattr(part, "text", ""))
            for part in content
        )
    return str(content or "")


llm = LLMClient()

__all__ = ["LLMClient", "LLMUnavailable", "llm"]
