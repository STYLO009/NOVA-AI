"""NOVA backend — configuration.

Every setting is read from environment variables.  ``backend/.env`` is loaded
with ``python-dotenv`` when present (see ``.env.example`` for the keys).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ (this file lives in backend/nova_backend/)
BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def _str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _int(key: str, default: int) -> int:
    try:
        raw = os.getenv(key)
        if raw is None or not raw.strip():
            return default
        return int(float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


class Settings:  # noqa: D101 - plain settings holder
    def __init__(self) -> None:
        # --- server -----------------------------------------------------
        self.host: str = _str("NOVA_HOST", "127.0.0.1")
        self.port: int = _int("NOVA_PORT", 8000)

        # --- workspace --------------------------------------------------
        self.workspace: Path = self._resolve_workspace()

        # --- model ------------------------------------------------------
        self.hf_token: str = _str("HF_TOKEN", "")
        self.hf_model: str = _str("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
        self.local_model: str = _str("NOVA_LOCAL_MODEL", "")

        # --- behaviour --------------------------------------------------
        self.confirmation_timeout: int = _int("CONFIRMATION_TIMEOUT_SECONDS", 180)
        self.powershell_timeout: int = _int("POWERSHELL_TIMEOUT_SECONDS", 30)
        self.enable_browser_automation: bool = _bool("ENABLE_BROWSER_AUTOMATION", True)
        self.log_level: str = _str("LOG_LEVEL", "INFO").upper()

        # --- vector database (Qdrant) ------------------------------------
        # Reachable server wins; otherwise NOVA falls back to the embedded
        # (local file) Qdrant mode so a bare `uvicorn` still has vector search.
        self.qdrant_url: str = _str("QDRANT_URL", "http://localhost:6333")
        self.qdrant_api_key: str = _str("QDRANT_API_KEY", "")
        self.qdrant_collection: str = _str("QDRANT_COLLECTION", "nova_memory")
        self.qdrant_local_path: Path = Path(
            _str("QDRANT_LOCAL_PATH", str(BACKEND_DIR / "data" / "qdrant"))
        )
        #: auto = HF embeddings when HF_TOKEN is set, deterministic hashing otherwise
        self.embedding_provider: str = _str("EMBEDDING_PROVIDER", "auto").lower()
        self.embedding_model: str = _str(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        self.embedding_dim: int = _int("EMBEDDING_DIM", 384)

        # --- auth --------------------------------------------------------
        self.auth_enabled: bool = _bool("NOVA_AUTH_ENABLED", True)
        self.session_ttl_hours: int = _int("SESSION_TTL_HOURS", 168)
        self.pbkdf2_iterations: int = _int("PBKDF2_ITERATIONS", 390000)
        #: Comma separated browser origins allowed to call the API.
        #: "*" (the default) opens CORS to every origin without credentials —
        #: auth still works because the frontend sends `Authorization: Bearer`.
        #: Set an explicit list (comma separated) to also enable credentialed
        #: (cookie) requests from those origins only.
        self.cors_origins: list[str] = [
            o.strip()
            for o in _str("NOVA_CORS_ORIGINS", "*").split(",")
            if o.strip()
        ]

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _resolve_workspace() -> Path:
        raw = _str("NOVA_WORKSPACE", "")
        if raw:
            path = Path(os.path.expandvars(os.path.expanduser(raw)))
        else:
            home = Path(os.path.expanduser("~"))
            # %USERPROFILE%\Documents\NOVA
            profile = os.environ.get("USERPROFILE")
            docs = home / "Documents"
            if profile:
                docs = Path(profile) / "Documents"
            path = docs / "NOVA"
        return path

    def ensure_workspace(self) -> Path:
        """Create the workspace folder on demand and return it."""
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - defensive
            pass
        return self.workspace

    @property
    def workspace_str(self) -> str:
        return str(self.workspace)

    @property
    def is_windows(self) -> bool:
        """Windows-only tools (app control, PowerShell) are gated on this."""
        import sys

        return sys.platform.startswith("win")


settings = Settings()


def setup_logging() -> None:
    """Configure root logging from ``LOG_LEVEL`` (idempotent)."""
    level = getattr(logging, settings.log_level, logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger().setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


__all__ = ["Settings", "settings", "setup_logging", "BACKEND_DIR"]
