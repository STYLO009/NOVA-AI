"""NOVA backend — Qdrant vector store for long-term memory.

Resolution order
----------------
1. ``QDRANT_URL`` server (``QDRANT_API_KEY`` optional) — the Docker setup.
2. Embedded Qdrant on disk (``QDRANT_LOCAL_PATH``) — a bare ``uvicorn`` run.
3. Disabled — every call degrades to ``None`` and :mod:`.store` falls back to
   SQL ``LIKE`` search, so memory never breaks because the vector DB is gone.

Embeddings
----------
``EMBEDDING_PROVIDER`` accepts ``auto`` (default), ``hf`` or ``hashing``:

* ``hf``      — Hugging Face inference embeddings (needs ``HF_TOKEN``).
* ``hashing`` — deterministic signed feature hashing over word 1-2 grams.
                No network, stable across restarts, good enough for recall.
* ``auto``    — ``hf`` when a token is configured, otherwise ``hashing``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from typing import Any, Optional

from ..config import settings

log = logging.getLogger("nova.vectors")

TOKEN_RE = re.compile(r"[a-z0-9]+")
MODE_OFF = "disabled"
MODE_SERVER = "server"
MODE_LOCAL = "local"


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


class HashingEmbedder:
    """Signed feature hashing — reproducible, dependency and network free."""

    def __init__(self, dim: int) -> None:
        self.dim = max(8, int(dim))

    @property
    def name(self) -> str:
        return f"hashing-{self.dim}"

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        grams = _tokens(text)
        # unigrams + bigrams: "delete" and "delete report" both carry signal
        features = grams + [f"{a}_{b}" for a, b in zip(grams, grams[1:])]
        if not features:
            features = ["<empty>"]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class HFEmbedder:
    """Mean-pooled Hugging Face feature extraction (requires ``HF_TOKEN``)."""

    def __init__(self, model: str, token: str) -> None:
        from huggingface_hub import InferenceClient  # lazy: optional dependency

        self.model = model
        self._client = InferenceClient(model=model, token=token)

    @property
    def name(self) -> str:
        return self.model

    def _pool(self, output: Any) -> list[float]:
        # InferenceClient returns [tokens][dim] for feature extraction.
        rows = output if isinstance(output, list) else []
        if rows and isinstance(rows[0], list) and isinstance(rows[0][0], (int, float)):
            dim = len(rows[0])
            acc = [0.0] * dim
            for row in rows:
                for i, value in enumerate(row):
                    acc[i] += float(value)
            norm = math.sqrt(sum(v * v for v in acc)) or 1.0
            return [v / norm for v in acc]
        if rows and isinstance(rows[0], (int, float)):
            norm = math.sqrt(sum(float(v) ** 2 for v in rows)) or 1.0
            return [float(v) / norm for v in rows]
        raise ValueError("unexpected embedding payload from the inference API")

    def embed(self, text: str) -> list[float]:
        return self._pool(self._client.feature_extraction(text or ""))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def build_embedder():
    """Returns ``(embedder, dim)`` honouring ``EMBEDDING_PROVIDER``."""
    dim = max(8, int(settings.embedding_dim))
    provider = (settings.embedding_provider or "auto").lower()
    want_hf = provider == "hf" or (provider == "auto" and bool(settings.hf_token))
    if want_hf:
        if not settings.hf_token:
            log.warning("EMBEDDING_PROVIDER=hf but HF_TOKEN is empty — hashing embeddings")
        else:
            try:
                return HFEmbedder(settings.embedding_model, settings.hf_token), dim
            except Exception as exc:  # pragma: no cover - network / import failure
                log.warning("HF embeddings unavailable (%s) — using hashing", exc)
    return HashingEmbedder(dim), dim


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class VectorStore:
    """Thin, failure-tolerant wrapper around a Qdrant collection."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client: Any = None
        self._mode: str = MODE_OFF
        self._dim: int = 0
        self._embedder: Any = None
        self._ensure_error: Optional[str] = None
        self._last_attempt: float = -1e9

    # -- lifecycle --------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def collection(self) -> str:
        return settings.qdrant_collection

    @property
    def embedder_name(self) -> str:
        return self._embedder.name if self._embedder else "none"

    def info(self) -> dict[str, Any]:
        """Shape reported by ``GET /api/health``."""
        payload: dict[str, Any] = {
            "mode": self._mode,
            "available": self.available,
            "collection": self.collection,
            "embeddings": self.embedder_name,
            "count": 0,
        }
        if not self.available:
            payload["error"] = self._ensure_error or "vector search not initialised"
            return payload
        try:
            payload["count"] = int(self._count())
        except Exception as exc:  # pragma: no cover
            payload["error"] = str(exc)
        return payload

    def _connect(self) -> None:
        try:
            from qdrant_client import QdrantClient
        except Exception as exc:  # pragma: no cover - dependency missing
            self._fail(f"qdrant-client is not installed ({exc})")
            return

        # 1 ── server mode --------------------------------------------------
        if settings.qdrant_url:
            try:
                client = QdrantClient(
                    url=settings.qdrant_url,
                    api_key=settings.qdrant_api_key or None,
                    timeout=3,
                )
                client.get_collections()  # forces the connection check
                self._client, self._mode = client, MODE_SERVER
                log.info("Qdrant server ready at %s", settings.qdrant_url)
                return
            except Exception as exc:
                log.info("Qdrant server unreachable (%s) — trying embedded mode", exc)

        # 2 ── embedded mode -------------------------------------------------
        try:
            path = str(settings.qdrant_local_path)
            settings.qdrant_local_path.mkdir(parents=True, exist_ok=True)
            client = QdrantClient(path=path)
            self._client, self._mode = client, MODE_LOCAL
            log.info("Qdrant embedded store ready at %s", path)
        except Exception as exc:
            self._fail(f"embedded Qdrant failed: {exc}")

    def _fail(self, reason: str) -> None:
        self._client = None
        self._mode = MODE_OFF
        self._ensure_error = reason
        log.warning("vector search disabled — %s", reason)

    def ensure(self) -> bool:
        """Force a connection attempt now.

        Called once during startup so ``GET /api/health`` reports the real
        vector mode from the first request — with an empty database nothing
        else would touch the store until the first memory write.
        """
        return self._ensure()

    def _ensure(self) -> bool:
        with self._lock:
            if self._client is not None:
                return True
            # retry a failed connect at most once every 60s — a dead Qdrant
            # must not add a 3s timeout to every memory write
            now = time.monotonic()
            if self._ensure_error and now - self._last_attempt < 60.0:
                return False
            self._last_attempt = now
            self._connect()
            if self._client is None:
                return False
            try:
                self._embedder, self._dim = build_embedder()
                self._ensure_collection()
                self._ensure_error = None
                return True
            except Exception as exc:
                self._fail(f"collection setup failed: {exc}")
                return False

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        name = self.collection
        existing = {c.name for c in self._client.get_collections().collections}
        if name not in existing:
            self._client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=self._dim, distance=models.Distance.COSINE
                ),
            )
            log.info("created Qdrant collection %s (dim=%s)", name, self._dim)
            return
        info = self._client.get_collection(name)
        current = int(info.config.params.vectors.size or 0)
        if current != self._dim:
            # dimension drift (provider changed) — rebuild instead of failing
            log.warning(
                "Qdrant collection %s has dim %s, expected %s — recreating",
                name,
                current,
                self._dim,
            )
            self._client.delete_collection(name)
            self._client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=self._dim, distance=models.Distance.COSINE
                ),
            )

    # -- operations -------------------------------------------------------
    def upsert(self, point_id: int | str, text: str, payload: Optional[dict] = None) -> bool:
        if not text or not self._ensure():
            return False
        try:
            from qdrant_client import models

            self._client.upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(
                        id=int(point_id)
                        if str(point_id).isdigit()
                        else _stable_id(str(point_id)),
                        vector=self._embedder.embed(text),
                        payload={"text": text, **(payload or {})},
                    )
                ],
                wait=True,
            )
            return True
        except Exception as exc:
            log.warning("qdrant upsert failed: %s", exc)
            return False

    def search(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float = 0.1,
        payload_filter: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """Ranked hits ``[{id, score, text, payload}]`` — ``[]`` when degraded."""
        if not query or not self._ensure():
            return []
        try:
            from qdrant_client import models

            must = [
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
                for key, value in (payload_filter or {}).items()
            ]
            hits = self._client.query_points(
                collection_name=self.collection,
                query=self._embedder.embed(query),
                limit=max(1, int(limit)),
                score_threshold=score_threshold,
                query_filter=models.Filter(must=must) if must else None,
                with_payload=True,
            ).points
            results: list[dict[str, Any]] = []
            for hit in hits:
                payload = dict(hit.payload or {})
                results.append(
                    {
                        "id": hit.id,
                        "score": round(float(hit.score), 4),
                        "text": str(payload.get("text") or ""),
                        "payload": payload,
                    }
                )
            return results
        except Exception as exc:
            log.warning("qdrant search failed: %s", exc)
            return []

    def delete(self, point_id: int | str) -> bool:
        if not self._ensure():
            return False
        try:
            from qdrant_client import models

            numeric = int(point_id) if str(point_id).isdigit() else _stable_id(str(point_id))
            self._client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=[numeric]),
                wait=True,
            )
            return True
        except Exception as exc:
            log.warning("qdrant delete failed: %s", exc)
            return False

    def _count(self) -> int:
        if self._client is None:
            return 0
        return int(self._client.count(self.collection, exact=True).count)


def _stable_id(text: str) -> int:
    """Deterministic 63 bit id for string keys (Qdrant needs uint64)."""
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 1


vectors = VectorStore()

__all__ = [
    "VectorStore",
    "vectors",
    "HashingEmbedder",
    "HFEmbedder",
    "build_embedder",
    "MODE_OFF",
    "MODE_SERVER",
    "MODE_LOCAL",
]
