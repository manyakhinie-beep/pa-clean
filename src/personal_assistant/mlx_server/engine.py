"""
MLX inference engine.

Loads a local MLX model once and exposes a simple generate() interface.
Works only on Apple Silicon (M1/M2/M3/M4).

Model path is set via PA_MLX_MODEL_PATH in .env or environment.
Example models (download with `mlx_lm.convert` or from mlx-community on HF):
  /Users/you/models/mlx-community/Mistral-7B-Instruct-v0.3-4bit
  /Users/you/models/mlx-community/Meta-Llama-3.1-8B-Instruct-4bit
  /Users/you/models/mlx-community/Phi-3.5-mini-instruct-4bit

Threading note
--------------
MLX GPU streams are thread-local.  FastAPI/AnyIO runs streaming generators
in a pool of worker threads.  If any two requests land in different threads,
the second one encounters "no Stream(gpu, N) in current thread" because the
GPU stream was initialised in a different thread.

Fix: a single, long-lived _MLXThread owns all GPU operations.  Any thread
may call engine.generate() / engine.stream(); the work is dispatched to the
MLX thread via a queue and results come back through another queue.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from loguru import logger

from personal_assistant.config import settings

# ---------------------------------------------------------------------------
# mlx_lm API compatibility shim
# The parameter name for sampling temperature has changed across versions:
#   <0.19  : temp=
#   0.19+  : temperature=  (but generate_step may not accept it)
#   newer  : sampler= (make_sampler helper)
# We detect which API is available at call time.
# ---------------------------------------------------------------------------


def _resolve_sampling(
    max_tokens: Optional[int],
    temperature: Optional[float],
    top_p: Optional[float],
) -> tuple[int, float, float]:
    """Resolve sampling parameters, falling back to configured defaults.

    This is the single place that makes the MLX engine config-driven instead of
    hardcoded: any caller that omits a parameter inherits the value set in the
    "Правила" (Rules) tab via :class:`personal_assistant.config.Settings`.

    :returns: ``(max_tokens, temperature, top_p)`` with ``None`` values filled
        in from ``settings.mlx_max_tokens`` / ``mlx_temperature`` / ``mlx_top_p``.
    """
    return (
        max_tokens or settings.mlx_max_tokens,
        temperature if temperature is not None else settings.mlx_temperature,
        top_p if top_p is not None else settings.mlx_top_p,
    )


def _make_sampler(temp: float, top_p: float):
    """Build an mlx_lm sampler if the helper is available, else ``None``.

    Resilient to mlx_lm API drift: only passes ``temp``/``temperature``/``top_p``
    if ``make_sampler`` actually accepts them.
    """
    try:
        import inspect

        from mlx_lm.sample_utils import make_sampler
    except ImportError:
        return None

    accepted = set(inspect.signature(make_sampler).parameters)
    mk: dict = {}
    if "temp" in accepted:
        mk["temp"] = temp
    elif "temperature" in accepted:
        mk["temperature"] = temp
    if "top_p" in accepted:
        mk["top_p"] = top_p
    return make_sampler(**mk)


def _apply_sampling_kwargs(fn_params: set, kwargs: dict, temp: float, top_p: float) -> None:
    """Add temperature/top_p to *kwargs* using whichever mlx_lm API is present.

    The sampling parameter API changed across mlx_lm versions:
      <0.19  : ``temp=``
      0.19+  : ``temperature=`` (and often ``top_p=``)
      0.21+  : ``sampler=`` built via ``make_sampler``
    """
    if "temp" in fn_params:
        kwargs["temp"] = temp
        if "top_p" in fn_params:
            kwargs["top_p"] = top_p
    elif "temperature" in fn_params:
        kwargs["temperature"] = temp
        if "top_p" in fn_params:
            kwargs["top_p"] = top_p
    else:
        sampler = _make_sampler(temp, top_p)
        if sampler is not None:
            kwargs["sampler"] = sampler  # carries both temp and top_p


def _mlx_generate(
    generate_fn,
    model,
    tokenizer,
    prompt: str,
    max_tokens: int,
    temp: float,
    top_p: float = 1.0,
) -> str:
    """Call mlx_lm.generate() regardless of which API version is installed."""
    import inspect

    sig = inspect.signature(generate_fn)
    params = set(sig.parameters)

    kwargs: dict = {"max_tokens": max_tokens, "verbose": False}
    _apply_sampling_kwargs(params, kwargs, temp, top_p)

    result = generate_fn(model, tokenizer, prompt=prompt, **kwargs)
    # mlx_lm >= 0.19 может вернуть GenerationResponse вместо строки
    if hasattr(result, "text"):
        return result.text
    return result


# ---------------------------------------------------------------------------
# _MLXThread — single dedicated thread that owns the MLX GPU context
# ---------------------------------------------------------------------------

_STOP_SENTINEL = object()


class _MLXThread(threading.Thread):
    """
    Singleton background thread for all MLX GPU operations.

    Why: MLX GPU streams are per-thread.  Starlette/AnyIO iterates streaming
    generators in a pool of short-lived worker threads.  Routing all GPU work
    through this single thread guarantees one stable GPU stream and eliminates
    "There is no Stream(gpu, N) in current thread" errors.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="pa-mlx-inference")
        self._task_q: queue.Queue = queue.Queue()
        self.start()

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901
        # Eagerly initialise the Metal GPU stream in *this* thread.
        try:
            import mlx.core as mx

            _dummy = mx.array([0.0])
            mx.eval(_dummy)
            del _dummy
            logger.debug("[mlx-thread] GPU stream initialised")
        except Exception as exc:
            logger.warning(f"[mlx-thread] GPU init skipped: {exc}")

        while True:
            task = self._task_q.get()
            if task is _STOP_SENTINEL:
                break
            fn, reply_q = task
            try:
                result = fn()
                reply_q.put(("ok", result))
            except Exception as exc:
                reply_q.put(("err", exc))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(self, fn) -> Any:
        """Run fn() in the MLX thread; block until result is ready."""
        rq: queue.Queue = queue.Queue()
        self._task_q.put((fn, rq))
        status, val = rq.get()
        if status == "err":
            raise val  # re-raise in the caller's thread
        return val

    def stream(self, gen_fn) -> Iterator[str]:
        """
        Run a *generator function* gen_fn() in the MLX thread.
        Yields each item produced by gen_fn back to the caller's thread.

        gen_fn must be a zero-argument callable that returns a generator.
        """
        chunk_q: queue.Queue = queue.Queue(maxsize=64)

        def _produce() -> None:
            try:
                for item in gen_fn():
                    chunk_q.put(("ok", item))
            except Exception as exc:
                chunk_q.put(("err", exc))
            finally:
                chunk_q.put(("done", None))

        rq: queue.Queue = queue.Queue()
        self._task_q.put((_produce, rq))

        # Consume chunks in the caller's thread (may be any thread)
        while True:
            tag, val = chunk_q.get()
            if tag == "ok":
                yield val
            elif tag == "err":
                raise val  # type: ignore[misc]
            else:  # "done"
                rq.get()  # wait for _produce to finish cleanly
                break


# Module-level singleton (lazy)
_mlx_thread: Optional[_MLXThread] = None
_mlx_thread_lock = threading.Lock()


def _get_mlx_thread() -> _MLXThread:
    global _mlx_thread
    if _mlx_thread is None:
        with _mlx_thread_lock:
            if _mlx_thread is None:
                _mlx_thread = _MLXThread()
    return _mlx_thread


# ---------------------------------------------------------------------------
# MLXEngine
# ---------------------------------------------------------------------------


class MLXEngine:
    """
    Lazy-loading wrapper around mlx_lm.

    All GPU operations are dispatched to the _MLXThread singleton so that
    the GPU stream is always initialised in the correct thread.

    Usage:
        engine = MLXEngine()
        response = engine.generate("Summarize: ...")
        # or with messages (chat format):
        response = engine.chat([{"role": "user", "content": "Hello"}])
    """

    # Cached availability check — evaluated once per process
    _mlx_available: Optional[bool] = None

    # Protects _ensure_loaded against concurrent first-load races
    _load_lock = threading.Lock()

    def __init__(self, model_path: Optional[str] = None) -> None:
        self._model_path = model_path or settings.mlx_model_path
        self._model: Any = None
        self._tokenizer: Any = None
        self._loaded = False
        # Eagerly check whether mlx-lm is importable so we can give a clear
        # message without crashing the server (install is optional on non-M1).
        if MLXEngine._mlx_available is None:
            try:
                import mlx_lm  # noqa: F401
                MLXEngine._mlx_available = True
            except ImportError:
                MLXEngine._mlx_available = False
                logger.warning(
                    "mlx-lm is not installed — LLM inference disabled. "
                    "To enable: uv pip install 'mlx-lm>=0.19.0'  (Apple Silicon only)"
                )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    # Constant returned by all generation methods when inference is unavailable
    _UNAVAILABLE_MSG = (
        "⚠️ Языковая модель недоступна. Причины и решения:\n"
        "• mlx-lm не установлен → запустите: uv pip install 'mlx-lm>=0.19.0'\n"
        "• PA_MLX_MODEL_PATH не задан → укажите путь к модели в .env\n"
        "• Модель не найдена → скачайте с https://huggingface.co/mlx-community\n"
        "• Нужен Apple Silicon (M1/M2/M3/M4) с нативным arm64 Python\n"
        "• Если Python запускается через Rosetta: rm -rf .venv && ./setup.sh\n\n"
        "Синхронизация, поиск и просмотр vault работают без модели."
    )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        with MLXEngine._load_lock:
            # Double-check after acquiring lock
            if self._loaded:
                return

            if not MLXEngine._mlx_available:
                raise RuntimeError(self._UNAVAILABLE_MSG)

            if not self._model_path:
                raise RuntimeError(
                    "MLX model path is not set. "
                    "Add PA_MLX_MODEL_PATH=/path/to/model to your .env file.\n"
                    "Download models: https://huggingface.co/mlx-community"
                )

            path = Path(self._model_path)
            if not path.exists():
                raise RuntimeError(f"Model path does not exist: {path}")

            if sys.platform != "darwin":
                raise RuntimeError("MLX requires macOS (Apple Silicon).")

            logger.info(f"Loading MLX model from {path} …")
            t0 = time.time()

            # Load in the dedicated MLX thread so the GPU stream is created there
            try:
                def _do_load():
                    from mlx_lm import load
                    loaded = load(str(path))
                    return loaded[0], loaded[1]

                self._model, self._tokenizer = _get_mlx_thread().call(_do_load)
            except ImportError:
                MLXEngine._mlx_available = False
                raise RuntimeError(self._UNAVAILABLE_MSG)

            self._loaded = True
            logger.info(f"Model loaded in {time.time() - t0:.1f}s")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_name(self) -> str:
        return Path(self._model_path).name if self._model_path else "not configured"

    # ------------------------------------------------------------------
    # Raw generation
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> str:
        """
        Generate a completion for a raw prompt string.
        Returns the generated text (without the prompt).

        ``max_tokens`` / ``temperature`` / ``top_p`` default to the configured
        values (``settings.mlx_*``) when left as ``None``.
        """
        self._ensure_loaded()
        assert self._model is not None
        assert self._tokenizer is not None

        max_tok, temp, top_p_eff = _resolve_sampling(max_tokens, temperature, top_p)

        model, tokenizer = self._model, self._tokenizer

        def _do() -> str:
            from mlx_lm import generate
            return _mlx_generate(generate, model, tokenizer, prompt, max_tok, temp, top_p_eff)

        t0 = time.time()
        result = _get_mlx_thread().call(_do)
        elapsed = time.time() - t0
        logger.debug(f"Generated {len(result.split())} words in {elapsed:.1f}s")
        return result

    # ------------------------------------------------------------------
    # Chat (preferred for instruction-tuned models)
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        system: Optional[str] = None,
        top_p: Optional[float] = None,
    ) -> str:
        """
        Generate a response in chat format.

        Returns _UNAVAILABLE_MSG (not raises) when mlx-lm is missing or model
        is not configured, so callers don't need to handle RuntimeError.
        """
        try:
            self._ensure_loaded()
        except RuntimeError as exc:
            logger.warning(f"[engine] chat() unavailable: {exc}")
            return self._UNAVAILABLE_MSG
        assert self._tokenizer is not None

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # Apply the model's chat template if available
        try:
            prompt = self._tokenizer.apply_chat_template(
                full_messages,
                add_generation_prompt=True,
                tokenize=False,
            )
        except Exception:
            # Fallback: simple concatenation for models without chat template
            prompt = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in full_messages
            )
            prompt += "\nASSISTANT:"

        return self.generate(
            prompt, max_tokens=max_tokens, temperature=temperature, top_p=top_p
        )

    # ------------------------------------------------------------------
    # Convenience: single-turn Q&A
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        context: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> str:
        """
        Single-turn question with optional context block.

        Args:
            question: the user question
            context: extra text to include before the question (vault content etc.)
            system: system prompt
        """
        if context:
            max_chars = settings.mlx_context_chars
            if len(context) > max_chars:
                context = context[:max_chars] + "\n\n[... контекст обрезан ...]"
            content = f"Контекст:\n{context}\n\n{question}"
        else:
            content = question

        return self.chat(
            messages=[{"role": "user", "content": content}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

    # ------------------------------------------------------------------
    # Streaming (для FastAPI StreamingResponse и интерактивного вывода)
    # ------------------------------------------------------------------

    def _make_stream_prompt(
        self,
        messages: list[dict],
        system: Optional[str] = None,
    ) -> str:
        """Собрать prompt-строку для стриминга (аналогично chat())."""
        self._ensure_loaded()
        assert self._tokenizer is not None
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        try:
            return self._tokenizer.apply_chat_template(
                full_messages,
                add_generation_prompt=True,
                tokenize=False,
            )
        except Exception:
            prompt = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in full_messages
            )
            return prompt + "\nASSISTANT:"

    def stream(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Iterator[str]:
        """
        Генерация с потоковым выводом токенов (generator).

        All GPU work is dispatched to the _MLXThread singleton to avoid
            RuntimeError: There is no Stream(gpu, N) in current thread
        which occurs when Starlette/AnyIO runs this generator in an arbitrary
        worker thread that has no MLX GPU stream.

        Yields _UNAVAILABLE_MSG as a single chunk when mlx-lm is unavailable,
        so StreamingResponse callers always get a readable response.
        """
        try:
            self._ensure_loaded()
        except RuntimeError as exc:
            logger.warning(f"[engine] stream() unavailable: {exc}")
            yield self._UNAVAILABLE_MSG
            return

        assert self._model is not None
        assert self._tokenizer is not None

        prompt = self._make_stream_prompt(messages, system)
        max_tok, temp, top_p_eff = _resolve_sampling(max_tokens, temperature, top_p)

        # Build kwargs for stream_generate (version-aware)
        import inspect

        from mlx_lm import stream_generate

        sig = inspect.signature(stream_generate)
        params = set(sig.parameters)
        kwargs: dict = {"max_tokens": max_tok}
        _apply_sampling_kwargs(params, kwargs, temp, top_p_eff)

        model, tokenizer = self._model, self._tokenizer

        # Generator function that runs entirely inside the MLX thread
        def _gen() -> Iterator[str]:
            # mlx_lm >= 0.19 yields GenerationResponse; older yields plain str.
            # GenerationResponse.text can be None on the final/stop response.
            for response in stream_generate(model, tokenizer, prompt=prompt, **kwargs):
                chunk = response.text if hasattr(response, "text") else response
                if isinstance(chunk, str) and chunk:
                    yield chunk

        # Dispatch to the single MLX thread; yield chunks back here
        yield from _get_mlx_thread().stream(_gen)

    def stream_ask(
        self,
        question: str,
        context: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Iterator[str]:
        """
        Потоковый вариант ask() — удобен для CLI и WebUI.
        """
        if context:
            max_chars = settings.mlx_context_chars
            if len(context) > max_chars:
                context = context[:max_chars] + "\n\n[... контекст обрезан ...]"
            content = f"Контекст:\n{context}\n\n{question}"
        else:
            content = question

        yield from self.stream(
            messages=[{"role": "user", "content": content}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, created on first import that uses it)
# ---------------------------------------------------------------------------

_engine: Optional[MLXEngine] = None


def get_engine() -> MLXEngine:
    """Return the shared MLXEngine singleton."""
    global _engine
    if _engine is None:
        _engine = MLXEngine()
    return _engine
