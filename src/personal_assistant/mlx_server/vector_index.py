"""
VectorIndex — numpy flat index + sentence-transformers для семантического поиска (Stage M2).

Принцип работы:
  1. build()         — генерирует embeddings, сохраняет в numpy (.npz) + pickle (метаданные)
  2. search()        — векторный поиск (cosine similarity через матричное умножение)
  3. hybrid_search() — BM25 + вектора → RRF объединение → top-k результатов

Хранилище: vault/.vector_index.npz  + vault/.vector_index_meta.pkl
  Нет зависимостей от lancedb — работает на любой платформе (darwin arm64, Intel, Linux).

Embedding backend: sentence-transformers
  — поддерживает любую HuggingFace-модель
  — загружает с диска (PA_EMBEDDING_MODEL_PATH) или скачивает автоматически
  — использует MPS (Apple Silicon GPU) если доступен

Рекомендуемые модели (для русского языка):
  BAAI/bge-m3                                    — лучший multilingual, dim=1024, ~570 MB
  sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  — быстрый, dim=384, ~120 MB
  intfloat/multilingual-e5-large                 — отличный multilingual, dim=1024, ~560 MB

Настройка (.env):
  PA_EMBEDDING_MODEL=BAAI/bge-m3                 # скачать с HuggingFace
  PA_EMBEDDING_MODEL_PATH=~/models/bge-m3        # или локальный путь

Пример:
    from personal_assistant.mlx_server.vault_index import VaultIndex
    from personal_assistant.mlx_server.vector_index import VectorIndex, hybrid_search

    bm25 = VaultIndex().load()
    vi = VectorIndex()
    vi.build(bm25.docs)
    results = hybrid_search("встреча проект", bm25, vi, top_k=5)
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from loguru import logger

from personal_assistant.config import settings

if TYPE_CHECKING:
    from personal_assistant.mlx_server.vault_index import VaultDoc, VaultIndex

# Файлы индекса (в корне vault, скрытые)
_IDX_VECTORS = ".vector_index.npz"     # float32 matrix (n, dim)
_IDX_META    = ".vector_index_meta.pkl"  # list[dict] — path, section, title, date, snippet
_MAX_TEXT_CHARS = 1000  # символов текста документа для embedding


# ---------------------------------------------------------------------------
# Рекомендуемые модели для команды pa list-models
# ---------------------------------------------------------------------------

RECOMMENDED_MODELS = [
    {
        "model": "BAAI/bge-m3",
        "dim": 1024,
        "size_gb": 0.57,
        "languages": "multilingual (100+)",
        "note": "лучшее качество для русского",
    },
    {
        "model": "intfloat/multilingual-e5-large",
        "dim": 1024,
        "size_gb": 0.56,
        "languages": "multilingual (100+)",
        "note": "отличный баланс качества",
    },
    {
        "model": "intfloat/multilingual-e5-base",
        "dim": 768,
        "size_gb": 0.28,
        "languages": "multilingual (100+)",
        "note": "быстрее, чуть хуже качество",
    },
    {
        "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "dim": 384,
        "size_gb": 0.12,
        "languages": "multilingual (50+)",
        "note": "самый быстрый, небольшой размер",
    },
    {
        "model": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "dim": 768,
        "size_gb": 0.28,
        "languages": "multilingual (50+)",
        "note": "хорошее качество",
    },
]


# ---------------------------------------------------------------------------
# Embedding model (sentence-transformers)
# ---------------------------------------------------------------------------


class EmbeddingModel:
    """
    Обёртка над sentence-transformers.

    Поддерживает любую HuggingFace-модель — с диска или по имени (авто-скачивание).
    Автоматически использует MPS (Apple Silicon) если доступен.

    Настройка через .env:
      PA_EMBEDDING_MODEL_PATH=~/models/bge-m3   # локальный путь (приоритет)
      PA_EMBEDDING_MODEL=BAAI/bge-m3            # имя модели на HuggingFace
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        model_path: Optional[str] = None,
    ) -> None:
        # Приоритет: явный аргумент → PA_EMBEDDING_MODEL_PATH → PA_EMBEDDING_MODEL
        _path = model_path or (settings.embedding_model_path.strip() or None)
        _name = model_name or settings.embedding_model.strip()

        if _path:
            _expanded = str(Path(_path).expanduser().resolve())
            self._model_id = _expanded  # путь передаём в SentenceTransformer
            self._model_label = f"local:{Path(_expanded).name}"
        elif _name:
            self._model_id = _name
            self._model_label = _name
        else:
            self._model_id = ""
            self._model_label = "(не задана)"

        self._model: Any = None
        self._dim: Optional[int] = None  # определяется после загрузки

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "sentence-transformers не установлен. Выполните: uv sync"
            )

        if not self._model_id:
            raise RuntimeError(
                "Embedding-модель не задана.\n"
                "Добавьте в .env одно из:\n"
                "  PA_EMBEDDING_MODEL=BAAI/bge-m3\n"
                "  PA_EMBEDDING_MODEL_PATH=~/models/bge-m3\n"
                "Полный список рекомендуемых моделей: uv run pa list-models"
            )

        # Выбираем устройство: MPS (Apple Silicon) → CPU
        device = _best_device()
        logger.info(
            f"Загрузка embedding-модели: {self._model_label} (device={device}) …"
        )

        # Если SSL-проверка отключена — пробрасываем через переменные окружения,
        # которые использует requests/huggingface_hub при скачивании модели
        if not settings.embedding_ssl_verify:
            import os as _os

            _os.environ["CURL_CA_BUNDLE"] = ""
            _os.environ["REQUESTS_CA_BUNDLE"] = ""
            # sentence-transformers использует requests → можно также отключить верификацию:
            try:
                import requests as _req

                _req.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
            except Exception:
                pass
            logger.warning(
                "Проверка SSL-сертификата HuggingFace отключена "
                "(PA_EMBEDDING_SSL_VERIFY=false). Используйте только в доверенной сети."
            )

        t0 = time.time()
        self._model = SentenceTransformer(self._model_id, device=device)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info(
            f"Embedding-модель загружена: dim={self._dim}, "
            f"device={device}, {time.time() - t0:.1f}с"
        )

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Кодировать список текстов → float32 array shape (n, dim)."""
        self._ensure_loaded()
        assert self._model is not None
        vecs = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=len(texts) > 100,
            normalize_embeddings=True,  # нормализуем для cosine similarity
            convert_to_numpy=True,
        )
        return vecs.astype(np.float32)

    def encode_one(self, text: str) -> list[float]:
        """Кодировать один текст → list[float]."""
        return self.encode([text])[0].tolist()

    @property
    def dim(self) -> Optional[int]:
        """Размерность векторов (доступна после первой загрузки)."""
        return self._dim

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def label(self) -> str:
        return self._model_label


def _best_device() -> str:
    """MPS (Apple Silicon) если доступен, иначе CPU."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# Модуль-уровневый синглтон
_embedding_model: Optional[EmbeddingModel] = None


def get_embedding_model() -> EmbeddingModel:
    """Вернуть общий экземпляр EmbeddingModel (читает настройки из settings)."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model


# ---------------------------------------------------------------------------
# Подготовка текста документа для embedding
# ---------------------------------------------------------------------------


def _doc_text(doc: "VaultDoc") -> str:
    """Собрать текст документа: title + snippet контента."""
    title = doc.title or ""
    content = doc.content.strip()[:_MAX_TEXT_CHARS]
    return f"{title}\n{content}".strip()


# ---------------------------------------------------------------------------
# VectorIndex (LanceDB)
# ---------------------------------------------------------------------------


class VectorIndex:
    """
    Numpy flat векторный индекс vault (без lancedb).

    Хранилище:
        vault/.vector_index.npz      — float32 матрица (n, dim), нормализованные векторы
        vault/.vector_index_meta.pkl — list[dict] с метаданными каждого документа

    Поиск: косинусное сходство через np.dot(vectors, query_vec).
    Векторы нормализованы при encode (normalize_embeddings=True), поэтому
    косинусное сходство = скалярное произведение.

    Жизненный цикл:
        vi = VectorIndex()
        vi.build(index.docs)      # построить индекс (занимает 1-5 мин)
        vi.search("запрос")       # семантический поиск
        vi.stats                  # статистика
        vi.invalidate()           # удалить индекс (для rebuild)
    """

    def __init__(self, vault_path: Optional[Path] = None) -> None:
        self.vault_path = Path(vault_path or settings.vault_path).expanduser()
        self._vec_file  = self.vault_path / _IDX_VECTORS
        self._meta_file = self.vault_path / _IDX_META
        # Кэш в памяти (None = не загружен)
        self._vectors: Optional[np.ndarray] = None   # shape (n, dim)
        self._meta: Optional[list[dict]]    = None   # list[dict]
        self.embedding = get_embedding_model()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> bool:
        """Загрузить индекс с диска. Возвращает True если успешно."""
        if self._vectors is not None:
            return True
        if not self._vec_file.exists() or not self._meta_file.exists():
            return False
        try:
            self._vectors = np.load(str(self._vec_file), allow_pickle=False)["vectors"]
            with open(self._meta_file, "rb") as f:
                self._meta = pickle.load(f)
            return True
        except Exception as exc:
            logger.warning(f"Не удалось загрузить векторный индекс: {exc}")
            self._vectors = None
            self._meta = None
            return False

    def _save(self, vectors: np.ndarray, meta: list[dict]) -> None:
        """Сохранить индекс на диск."""
        self.vault_path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(self._vec_file), vectors=vectors)
        with open(self._meta_file, "wb") as f:
            pickle.dump(meta, f, protocol=pickle.HIGHEST_PROTOCOL)
        # Обновляем кэш
        self._vectors = vectors
        self._meta    = meta

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_built(self) -> bool:
        """True если индекс существует на диске и содержит документы."""
        try:
            return self._load() and self._vectors is not None and len(self._vectors) > 0
        except Exception:
            return False

    def build(self, docs: list["VaultDoc"], batch_size: int = 32) -> int:
        """
        Построить (или перестроить) векторный индекс.

        Генерирует embeddings батчами и сохраняет в .npz + .pkl.
        Возвращает количество проиндексированных документов.

        Время: ~1-5 мин на 1000 документов (зависит от процессора).
        """
        if not docs:
            logger.warning("Нет документов для индексации")
            return 0

        logger.info(f"Построение векторного индекса: {len(docs)} документов…")
        t0 = time.time()

        # Генерация embeddings батчами
        texts = [_doc_text(d) for d in docs]
        vectors = self.embedding.encode(texts, batch_size=batch_size)  # (n, dim) float32

        # Метаданные — всё кроме вектора
        meta = [
            {
                "path":    str(doc.path),
                "section": doc.section or "",
                "title":   doc.title or "",
                "date":    str(doc.date) if doc.date is not None else "",
                "snippet": doc.short_summary(200) or "",
            }
            for doc in docs
        ]

        self._save(vectors, meta)
        elapsed = time.time() - t0

        logger.info(
            f"Векторный индекс построен: {len(meta)} docs "
            f"за {elapsed:.1f}с ({len(meta) / max(elapsed, 0.1):.0f} docs/с)"
        )
        return len(meta)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """
        Семантический поиск по косинусному сходству.

        Returns:
            list[(path_str, score)] отсортированный по убыванию score ∈ [0, 1].
            score = cosine similarity (dot-product нормализованных векторов).
        """
        if not self._load():
            logger.warning("Векторный индекс не построен. Запустите: pa build-index")
            return []

        assert self._vectors is not None
        assert self._meta    is not None

        query_arr = np.asarray(self.embedding.encode_one(query), dtype=np.float32)
        # cosine similarity = dot product (оба вектора нормализованы)
        scores: np.ndarray = self._vectors @ query_arr  # shape (n,)

        # Берём top_k лучших индексов (argsort по убыванию)
        k = min(top_k, len(scores))
        top_idx = np.argpartition(scores, -k)[-k:]
        top_idx = top_idx[np.argsort(-scores[top_idx])]

        return [(self._meta[i]["path"], float(scores[i])) for i in top_idx]

    def invalidate(self) -> None:
        """Удалить векторный индекс (перед rebuild или при смене vault)."""
        for f in (self._vec_file, self._meta_file):
            if f.exists():
                f.unlink()
                logger.debug(f"Удалён файл индекса: {f}")
        self._vectors = None
        self._meta    = None

    @property
    def stats(self) -> dict:
        """Статистика: построен ли индекс, сколько документов, пути к файлам."""
        try:
            built = self._load() and self._vectors is not None
            return {
                "built":    built,
                "docs":     len(self._vectors) if built and self._vectors is not None else 0,
                "vec_file": str(self._vec_file),
                "meta_file": str(self._meta_file),
                "model":    self.embedding.label,
            }
        except Exception:
            return {
                "built":    False,
                "docs":     0,
                "vec_file": str(self._vec_file),
                "meta_file": str(self._meta_file),
            }


# ---------------------------------------------------------------------------
# Hybrid search: BM25 + Vector → RRF
# ---------------------------------------------------------------------------


def hybrid_search(
    query: str,
    bm25_index: "VaultIndex",
    vector_index: VectorIndex,
    top_k: int = 10,
    bm25_candidates: int = 20,
    vector_candidates: int = 20,
    rrf_k: int = 60,
    sections: Optional[list[str]] = None,
) -> list["VaultDoc"]:
    """
    Гибридный поиск: BM25 + векторный поиск → RRF объединение.

    Алгоритм:
      1. BM25-поиск → top-N кандидатов (ключевые слова)
      2. Векторный поиск → top-N кандидатов (семантика)
      3. RRF (Reciprocal Rank Fusion): score(doc) = Σ 1/(k + rank)
      4. Сортировка по суммарному RRF-score → top-k результатов

    Преимущество над чистым BM25:
      - Ловит семантические синонимы («встреча» ↔ «звонок», «дедлайн» ↔ «срок»)
      - Работает при запросах на другом языке, чем документ
      - Устойчив к опечаткам и перефразировкам

    Args:
        query:             поисковый запрос (естественный язык)
        bm25_index:        загруженный VaultIndex (BM25)
        vector_index:      построенный VectorIndex (numpy flat index)
        top_k:             итоговое количество документов
        bm25_candidates:   размер пула кандидатов от BM25
        vector_candidates: размер пула кандидатов от векторного поиска
        rrf_k:             параметр сглаживания RRF (обычно 60)
        sections:          фильтр по секциям: ["calendar", "mail", "contacts"]

    Returns:
        list[VaultDoc], отсортированный по RRF-score (лучшие первые)
    """
    # 1. BM25 кандидаты
    bm25_docs = bm25_index.search(query, sections=sections, top_k=bm25_candidates)

    # 2. Векторные кандидаты (с фильтрацией по секции)
    path_to_doc = {str(d.path): d for d in bm25_index.docs}
    vector_hits = vector_index.search(query, top_k=vector_candidates)
    vector_docs: list["VaultDoc"] = []
    for path_str, _score in vector_hits:
        doc = path_to_doc.get(path_str)
        if doc is None:
            continue
        if sections and doc.section not in sections:
            continue
        vector_docs.append(doc)

    # 3. RRF fusion
    rrf_scores: dict[str, float] = {}
    for rank, doc in enumerate(bm25_docs):
        key = str(doc.path)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)
    for rank, doc in enumerate(vector_docs):
        key = str(doc.path)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)

    # 4. Сбор всех уникальных документов
    all_docs: dict[str, "VaultDoc"] = {}
    for doc in bm25_docs:
        all_docs[str(doc.path)] = doc
    for doc in vector_docs:
        all_docs.setdefault(str(doc.path), doc)

    # 5. Сортировка по RRF-score
    ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])
    return [all_docs[path] for path, _ in ranked[:top_k] if path in all_docs]


# ---------------------------------------------------------------------------
# Модуль-уровневый синглтон
# ---------------------------------------------------------------------------

_vector_index: Optional[VectorIndex] = None


def get_vector_index(vault_path: Optional[Path] = None) -> Optional[VectorIndex]:
    """
    Вернуть VectorIndex если построен, иначе None.
    Используется в server.py для автоматического выбора hybrid/bm25.
    """
    global _vector_index
    if _vector_index is None:
        vi = VectorIndex(vault_path)
        if vi.is_built():
            _vector_index = vi
    return _vector_index


def reset_vector_index() -> None:
    """Сбросить синглтон (вызывается после rebuild)."""
    global _vector_index
    _vector_index = None
