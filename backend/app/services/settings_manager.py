"""Runtime-mutable settings manager.

Provides a thin layer above the static `settings` object loaded from .env so
that a few user-facing values (RAG thresholds, rerank toggle, etc.) can be
edited from the UI and survive process restarts without touching .env.

Storage: a single-row-per-key table (AppSetting) in the local SQLite DB.
Reads: in-memory cache, refreshed lazily on get().
Writes: PATCH-style; only known keys (the whitelist) are accepted.

The cache is consulted by retrieval code via ``settings_manager.get(key, fallback)``.
Keys not overridden fall back to whatever ``core.config.settings`` carries.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Dict, Iterable

from sqlalchemy.orm import Session

from ..core.config import settings as static_settings
from ..core.logging_config import logger
from ..models.database import AppSetting, get_sync_db

# Whitelist of user-editable RAG/retrieval settings. Each entry maps the
# external key to a (default-from-static-settings attribute, type-coercion fn).
RAG_KEYS: Dict[str, tuple[str, type]] = {
    "min_results": ("RAG_MIN_RESULTS", int),
    "max_results": ("RAG_MAX_RESULTS", int),
    "use_rerank": ("USE_RERANK", bool),
    "citation_coverage_min": ("CITATION_COVERAGE_MIN", int),
    "hybrid_candidates": ("HYBRID_CANDIDATES", int),
    "rerank_threshold": ("RERANK_THRESHOLD", float),
    "rrf_k": ("RRF_K", int),
    "min_grounding": ("MIN_GROUNDING", float),
}


def _coerce(value: Any, t: type) -> Any:
    if t is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if t is int:
        return int(value)
    if t is float:
        return float(value)
    return value


class SettingsManager:
    _instance: "SettingsManager | None" = None
    _instance_lock = Lock()

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._lock = Lock()
        self._loaded = False

    @classmethod
    def get_instance(cls) -> "SettingsManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = SettingsManager()
        return cls._instance

    def load(self) -> None:
        """Load all stored overrides into the in-memory cache."""
        db: Session = next(get_sync_db())
        try:
            rows = db.query(AppSetting).all()
            with self._lock:
                self._cache = {r.key: r.value_json for r in rows}
                self._loaded = True
            logger.info(f"SettingsManager loaded {len(rows)} overrides")
        except Exception as e:
            logger.warning(f"SettingsManager load failed (will use defaults): {e}")
        finally:
            db.close()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def get_rag_settings(self) -> Dict[str, Any]:
        """Return the effective values for every RAG key, applying overrides."""
        self._ensure_loaded()
        out: Dict[str, Any] = {}
        for ext_key, (attr, t) in RAG_KEYS.items():
            if ext_key in self._cache:
                out[ext_key] = _coerce(self._cache[ext_key], t)
            else:
                out[ext_key] = _coerce(getattr(static_settings, attr), t)
        return out

    def update_rag_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a subset of RAG keys and return the resulting effective values."""
        valid_patch: Dict[str, Any] = {}
        for k, v in patch.items():
            if k not in RAG_KEYS:
                continue
            _, t = RAG_KEYS[k]
            try:
                valid_patch[k] = _coerce(v, t)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid value for {k}: {v!r}")

        if not valid_patch:
            return self.get_rag_settings()

        db: Session = next(get_sync_db())
        try:
            for k, v in valid_patch.items():
                row = db.query(AppSetting).filter(AppSetting.key == k).one_or_none()
                if row is None:
                    db.add(AppSetting(key=k, value_json=v))
                else:
                    row.value_json = v
            db.commit()
        finally:
            db.close()

        with self._lock:
            self._cache.update(valid_patch)
        return self.get_rag_settings()

    def known_keys(self) -> Iterable[str]:
        return RAG_KEYS.keys()
