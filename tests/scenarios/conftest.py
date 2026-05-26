"""
Shared session-scoped fixtures for scenario tests.

These fixtures are expensive (model loading, index building) and are shared
across all scenario test modules to avoid redundant work and GPU threading
conflicts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Session-scoped MLX engine
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def session_mlx_engine() -> Any:
    """Load the real MLX model once per test session."""
    if sys.platform != "darwin":
        pytest.skip("MLX requires macOS")
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        pytest.skip("mlx-lm not installed")

    from personal_assistant.config import settings
    from tests.conftest import ORIG_PA_MLX_MODEL_PATH

    model_path = ORIG_PA_MLX_MODEL_PATH.strip() or settings.mlx_model_path
    if not model_path or not Path(model_path).exists():
        pytest.skip("PA_MLX_MODEL_PATH not set or invalid")
    # The root conftest blanks mlx_model_path for safety (so unit/e2e never load
    # the model); restore it here so scenario tests load the configured model.
    settings.mlx_model_path = model_path

    from personal_assistant.mlx_server.engine import MLXEngine

    MLXEngine._mlx_available = None
    eng = MLXEngine()
    eng._ensure_loaded()
    return eng


# ---------------------------------------------------------------------------
# Session-scoped embedding model
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def session_embedding_model() -> Any:
    """Load embedding model once per session if configured."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        pytest.skip("sentence-transformers not installed")

    from personal_assistant.config import settings
    from tests.conftest import ORIG_PA_EMBEDDING_MODEL, ORIG_PA_EMBEDDING_MODEL_PATH

    model_name = (ORIG_PA_EMBEDDING_MODEL or settings.embedding_model or "").strip()
    model_path = (ORIG_PA_EMBEDDING_MODEL_PATH or settings.embedding_model_path or "").strip()

    if not model_name and not model_path:
        pytest.skip("No embedding model configured (PA_EMBEDDING_MODEL / PA_EMBEDDING_MODEL_PATH)")
    # Restore (root conftest blanks these for unit/e2e safety).
    settings.embedding_model = model_name
    settings.embedding_model_path = model_path

    model_id = model_path or model_name
    try:
        model = SentenceTransformer(model_id)
    except Exception as exc:
        pytest.skip(f"Failed to load embedding model {model_id}: {exc}")

    return model
