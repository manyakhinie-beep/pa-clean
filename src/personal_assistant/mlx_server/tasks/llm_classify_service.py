"""
LLM-assisted Semantic Classification Service (Stage 8).

When rule-based classification confidence is too low (< threshold), delegates
to the MLX engine for semantic understanding. Results are cached by SHA-256 to
avoid re-classifying unchanged documents.

Public API:
  - compute_rule_confidence(text, classifiers_cfg) -> float
  - needs_llm_classification(text, classifiers_cfg, threshold) -> bool
  - llm_classify_single(subject, preview, config, engine) -> LLMClassifyResult
  - batch_llm_classify_vault(vault_path, engine, config, threshold, batch_size)
  - LLMClassifyCache — persistent SHA-256-keyed cache
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from personal_assistant.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_FILE_NAME = "llm_classify_cache.json"
_CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class LLMClassifyResult:
    """Result of LLM semantic classification for a single document.

    :param doc_id: Unique doc identifier (SHA-256 of subject+preview).
    :param category: Assigned semantic category.
    :param confidence: Engine-reported or heuristic confidence [0, 1].
    :param raw_response: Verbatim LLM output.
    :param cached: Whether this result was retrieved from cache.
    :param error: Error message if classification failed.
    """

    doc_id: str
    category: str = ""
    confidence: float = 0.0
    raw_response: str = ""
    cached: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "category": self.category,
            "confidence": self.confidence,
            "raw_response": self.raw_response,
            "cached": self.cached,
            "error": self.error,
        }


@dataclass
class BatchLLMClassifyResult:
    """Aggregate result of a batch LLM classification run.

    :param total: Number of documents considered.
    :param classified: Number of documents that were classified.
    :param cached_hits: Number of cache hits (no LLM call needed).
    :param errors: Number of failed classifications.
    :param results: Per-document results.
    :param duration_seconds: Wall-clock time for the batch.
    """

    total: int = 0
    classified: int = 0
    cached_hits: int = 0
    errors: int = 0
    results: list[LLMClassifyResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "classified": self.classified,
            "cached_hits": self.cached_hits,
            "errors": self.errors,
            "duration_seconds": round(self.duration_seconds, 2),
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class LLMClassifyCache:
    """Persistent SHA-256-keyed cache for LLM classification results.

    Stored as JSON at ``data/llm_classify_cache.json`` (next to classify.yaml).
    Key: sha256(subject + "\\n" + preview[:500])
    Value: {"category": str, "confidence": float, "timestamp": float}
    """

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        self._path: Path = cache_path or (
            settings.classify_config_file.parent / _CACHE_FILE_NAME
        )
        self._data: dict[str, dict] = {}
        self._dirty: bool = False
        self._load()

    # ── Private ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION:
                    self._data = raw.get("entries", {})
                    logger.debug(
                        f"LLMClassifyCache loaded {len(self._data)} entries from {self._path}"
                    )
                else:
                    logger.info("LLMClassifyCache: version mismatch, starting fresh")
                    self._data = {}
            except Exception as exc:
                logger.warning(f"LLMClassifyCache load error ({self._path}): {exc}")
                self._data = {}

    def _save(self) -> None:
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": _CACHE_VERSION, "entries": self._data}
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._dirty = False
            logger.debug(f"LLMClassifyCache saved {len(self._data)} entries")
        except Exception as exc:
            logger.warning(f"LLMClassifyCache save error: {exc}")

    # ── Public ───────────────────────────────────────────────────────────────

    @staticmethod
    def make_key(subject: str, preview: str) -> str:
        """Compute SHA-256 cache key from document subject + preview."""
        content = f"{subject}\n{preview[:500]}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        """Return cached entry or None."""
        return self._data.get(key)

    def put(self, key: str, category: str, confidence: float) -> None:
        """Store a classification result in cache."""
        self._data[key] = {
            "category": category,
            "confidence": confidence,
            "timestamp": time.time(),
        }
        self._dirty = True

    def flush(self) -> None:
        """Persist pending writes to disk."""
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict:
        return {
            "total_entries": len(self._data),
            "cache_path": str(self._path),
        }


# ---------------------------------------------------------------------------
# Rule confidence
# ---------------------------------------------------------------------------


def compute_rule_confidence(text: str, classifiers_cfg: dict) -> float:
    """Compute fraction of classifiers that matched at least one keyword.

    :param text: Document subject + body text to scan.
    :param classifiers_cfg: The ``classifiers:`` section of classify.yaml.
    :returns: Float [0, 1] — 1.0 means every classifier matched.

    The score is ``matched_classifiers / total_classifiers``.
    A classifier is "matched" if any of its labels has at least one keyword hit.
    """
    if not classifiers_cfg:
        return 0.0

    text_lower = text.lower()
    total = len(classifiers_cfg)
    matched = 0

    for _classifier_name, label_rules in classifiers_cfg.items():
        if not isinstance(label_rules, dict):
            total -= 1
            continue
        classifier_matched = False
        for _label, rules in label_rules.items():
            if not isinstance(rules, dict):
                continue
            keywords = rules.get("keywords", [])
            contacts = rules.get("contacts", [])
            if any(kw.lower() in text_lower for kw in keywords) or any(
                c.lower() in text_lower for c in contacts
            ):
                classifier_matched = True
                break
        if classifier_matched:
            matched += 1

    return matched / total if total > 0 else 0.0


def needs_llm_classification(
    text: str,
    classifiers_cfg: dict,
    threshold: float = 0.4,
) -> bool:
    """Return True if rule-based confidence is below *threshold*.

    :param text: Document text.
    :param classifiers_cfg: classifiers section from config.
    :param threshold: Confidence cutoff — docs below this get LLM pass.
    """
    score = compute_rule_confidence(text, classifiers_cfg)
    return score < threshold


# ---------------------------------------------------------------------------
# Single-document LLM classification
# ---------------------------------------------------------------------------

#: Default categories offered to the LLM when none are configured.
_DEFAULT_CATEGORIES = [
    "urgent",
    "important",
    "meeting",
    "finance",
    "legal",
    "travel",
    "hr",
    "project",
    "it",
    "info",
]

#: Default Russian prompt template (placeholders: {subject}, {preview}, {categories})
_DEFAULT_PROMPT_TEMPLATE = (
    "Классифицируй письмо. Ответь ТОЛЬКО одним словом из списка:\n"
    "{categories}\n\n"
    "Тема: {subject}\n"
    "Письмо: {preview}\n\n"
    "Категория:"
)


def _build_prompt(
    subject: str,
    preview: str,
    categories: list[str],
    prompt_template: Optional[str] = None,
) -> str:
    """Build the classification prompt string."""
    template = prompt_template or _DEFAULT_PROMPT_TEMPLATE
    cats = ", ".join(categories)
    # Truncate preview to avoid exceeding context window
    preview_short = preview[:600].replace("\n", " ").strip()
    return template.format(
        subject=subject,
        preview=preview_short,
        categories=cats,
    )


def llm_classify_single(
    subject: str,
    preview: str,
    config: dict,
    engine,  # MLXEngine — avoid circular import with string annotation
    *,
    cache: Optional[LLMClassifyCache] = None,
) -> LLMClassifyResult:
    """Classify a single document using the MLX engine.

    :param subject: Document subject/title.
    :param preview: First ~600 chars of body text.
    :param config: Full classify config dict (reads ``llm_classify`` section).
    :param engine: Active :class:`MLXEngine` instance.
    :param cache: Optional :class:`LLMClassifyCache` to use. If provided, checks
        for a cached result first, and writes the result on success.
    :returns: :class:`LLMClassifyResult`.
    """
    llm_cfg = config.get("llm_classify", {})
    categories = llm_cfg.get("categories", _DEFAULT_CATEGORIES)
    prompt_template = llm_cfg.get("prompt")

    key = LLMClassifyCache.make_key(subject, preview)

    # Cache hit
    if cache is not None:
        cached = cache.get(key)
        if cached:
            return LLMClassifyResult(
                doc_id=key,
                category=cached["category"],
                confidence=cached.get("confidence", 1.0),
                raw_response=cached["category"],
                cached=True,
            )

    # Build and call
    prompt = _build_prompt(subject, preview, categories, prompt_template)
    try:
        raw = engine.ask(
            question=prompt,
            max_tokens=10,
            temperature=0.05,
        )
    except Exception as exc:
        logger.warning(f"LLM classify error for {subject!r}: {exc}")
        return LLMClassifyResult(doc_id=key, error=str(exc))

    # Parse response — expect a single word/label
    raw_stripped = raw.strip().split("\n")[0].strip().lower()
    # Match against known categories (prefix match to handle minor variations)
    matched_category = ""
    for cat in categories:
        if cat.lower() in raw_stripped or raw_stripped.startswith(cat.lower()):
            matched_category = cat
            break
    if not matched_category and raw_stripped:
        # Use as-is if no match (might be a valid novel label)
        matched_category = raw_stripped[:32]

    # Heuristic confidence: 1.0 if exact match, 0.7 if prefix/substr match
    exact = matched_category.lower() == raw_stripped
    confidence = 1.0 if exact else 0.7

    result = LLMClassifyResult(
        doc_id=key,
        category=matched_category,
        confidence=confidence,
        raw_response=raw.strip(),
        cached=False,
    )

    # Cache the successful result
    if cache is not None and matched_category:
        cache.put(key, matched_category, confidence)

    return result


# ---------------------------------------------------------------------------
# Batch classification
# ---------------------------------------------------------------------------


def batch_llm_classify_vault(
    vault_path: Path,
    engine,  # MLXEngine
    config: dict,
    *,
    threshold: float = 0.4,
    batch_size: int = 5,
    cache: Optional[LLMClassifyCache] = None,
    sections: Optional[list[str]] = None,
    dry_run: bool = False,
) -> BatchLLMClassifyResult:
    """Scan vault for low-confidence docs and classify them via LLM.

    Walks ``vault_path`` for ``.md`` files in *sections* subdirectories,
    computes rule confidence for each, and calls the LLM for those below
    *threshold*. Processes in batches of *batch_size*, persisting the cache
    after each batch.

    :param vault_path: Root path of the personal vault.
    :param engine: Active :class:`MLXEngine`.
    :param config: Full classify config dict.
    :param threshold: Rule confidence below which LLM is triggered.
    :param batch_size: Docs per LLM batch (persists cache after each batch).
    :param cache: LLMClassifyCache instance (auto-created if None).
    :param sections: Vault subdirs to scan (default: ``["mail", "outlook"]``).
    :param dry_run: If True, runs confidence scoring but skips LLM calls.
    :returns: :class:`BatchLLMClassifyResult`.
    """
    t0 = time.time()

    if cache is None:
        cache = LLMClassifyCache()

    classifiers_cfg = config.get("classifiers", {})
    target_sections = sections or ["mail", "outlook", "calendar"]

    # Collect candidate .md files
    candidates: list[Path] = []
    for section in target_sections:
        section_path = vault_path / section
        if section_path.is_dir():
            candidates.extend(section_path.rglob("*.md"))

    logger.info(
        f"batch_llm_classify: scanning {len(candidates)} docs "
        f"(threshold={threshold}, batch_size={batch_size}, dry_run={dry_run})"
    )

    batch_result = BatchLLMClassifyResult(total=len(candidates))
    pending: list[tuple[Path, str, str]] = []  # (path, subject, preview)

    # First pass: filter to docs needing LLM classification
    for md_path in candidates:
        try:
            raw = md_path.read_text(encoding="utf-8")
            subject, preview = _extract_subject_preview(raw)
            text = f"{subject}\n{preview}"

            # Check cache first
            key = LLMClassifyCache.make_key(subject, preview)
            cached = cache.get(key)
            if cached:
                batch_result.cached_hits += 1
                batch_result.classified += 1
                batch_result.results.append(
                    LLMClassifyResult(
                        doc_id=key,
                        category=cached["category"],
                        confidence=cached.get("confidence", 1.0),
                        cached=True,
                    )
                )
                continue

            if needs_llm_classification(text, classifiers_cfg, threshold):
                pending.append((md_path, subject, preview))
        except Exception as exc:
            logger.warning(f"Error reading {md_path}: {exc}")

    logger.info(
        f"batch_llm_classify: {len(pending)} docs need LLM, "
        f"{batch_result.cached_hits} from cache"
    )

    if dry_run or not pending:
        batch_result.duration_seconds = time.time() - t0
        cache.flush()
        return batch_result

    # Second pass: classify in batches
    for i in range(0, len(pending), batch_size):
        chunk = pending[i : i + batch_size]
        logger.info(
            f"LLM classify batch {i // batch_size + 1}/{(len(pending) - 1) // batch_size + 1} "
            f"({len(chunk)} docs)"
        )
        for md_path, subject, preview in chunk:
            res = llm_classify_single(
                subject, preview, config, engine, cache=cache
            )
            batch_result.results.append(res)
            if res.error:
                batch_result.errors += 1
            elif res.category:
                batch_result.classified += 1
                # Write llm_category tag back to frontmatter
                _write_llm_tag(md_path, res.category)

        # Persist cache after each batch
        cache.flush()

    batch_result.duration_seconds = time.time() - t0
    logger.info(
        f"batch_llm_classify done: {batch_result.classified} classified, "
        f"{batch_result.errors} errors, {batch_result.duration_seconds:.1f}s"
    )
    return batch_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_subject_preview(md_raw: str) -> tuple[str, str]:
    """Extract title and first 600 chars of body from a vault .md file.

    Handles YAML frontmatter (---...---) and falls back to first line.
    """
    subject = ""
    body_start = 0

    if md_raw.startswith("---"):
        end = md_raw.find("\n---", 3)
        if end != -1:
            fm_text = md_raw[3:end]
            body_start = end + 4
            try:
                fm = yaml.safe_load(fm_text) or {}
                subject = str(fm.get("subject") or fm.get("title") or "")
            except Exception:
                pass

    body = md_raw[body_start:].strip()

    if not subject:
        # Fallback: first non-empty line
        for line in md_raw.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                subject = stripped[:120]
                break

    preview = body[:600]
    return subject, preview


def _write_llm_tag(md_path: Path, category: str) -> None:
    """Append ``llm_category:<category>`` tag to a document's frontmatter."""
    tag = f"llm_category:{category}"
    ai_badge = "ai_classified"
    try:
        raw = md_path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return

        end = raw.find("\n---", 3)
        if end == -1:
            return

        fm_text = raw[3:end]
        body = raw[end + 4 :]

        fm = yaml.safe_load(fm_text) or {}
        existing = fm.get("tags", [])
        if isinstance(existing, str):
            existing = [existing]
        existing_set = set(str(t) for t in existing)

        # Remove stale llm_category tags
        cleaned = [t for t in existing_set if not str(t).startswith("llm_category:")]
        merged = sorted(set(cleaned) | {tag, ai_badge})
        fm["tags"] = merged

        new_fm = yaml.dump(
            fm, allow_unicode=True, default_flow_style=False, sort_keys=False
        ).rstrip("\n")
        md_path.write_text(f"---\n{new_fm}\n---{body}", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"_write_llm_tag failed for {md_path}: {exc}")


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------


def get_classify_stats(vault_path: Path, cache: Optional[LLMClassifyCache] = None) -> dict:
    """Return classification statistics for the vault.

    :param vault_path: Vault root directory.
    :param cache: Optional cache instance for cache stats.
    :returns: Dict with counts of classified docs, category distribution, etc.
    """
    if cache is None:
        cache = LLMClassifyCache()

    # Count AI-classified docs by scanning for 'ai_classified' tag
    ai_count = 0
    category_dist: dict[str, int] = {}
    total_md = 0

    for md_path in vault_path.rglob("*.md"):
        total_md += 1
        try:
            raw = md_path.read_text(encoding="utf-8")
            if not raw.startswith("---"):
                continue
            end = raw.find("\n---", 3)
            if end == -1:
                continue
            fm = yaml.safe_load(raw[3:end]) or {}
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            tag_strs = [str(t) for t in tags]
            if "ai_classified" in tag_strs:
                ai_count += 1
            for t in tag_strs:
                if t.startswith("llm_category:"):
                    cat = t.split(":", 1)[1]
                    category_dist[cat] = category_dist.get(cat, 0) + 1
        except Exception:
            pass

    return {
        "total_docs": total_md,
        "ai_classified": ai_count,
        "category_distribution": category_dist,
        "cache": cache.stats(),
    }
