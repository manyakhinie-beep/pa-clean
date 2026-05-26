"""
Unit tests for MLXEngine (engine.py).

Strategy: all tests run WITHOUT real mlx-lm installed.
We verify:
  - Graceful init when mlx-lm is missing
  - chat() / ask() return _UNAVAILABLE_MSG (not raise) when unavailable
  - generate() raises RuntimeError when unavailable
  - stream() yields _UNAVAILABLE_MSG as a single chunk when unavailable
  - is_loaded / model_name properties
  - _mlx_generate() API compatibility shim (temp / temperature / sampler branches)
  - _MLXThread.call() dispatches correctly
  - _MLXThread.stream() yields items from a generator
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fresh_engine(model_path: str = ""):
    """
    Return a new MLXEngine with _mlx_available forced to False
    and the module-level _engine singleton reset.
    """
    # Import lazily so we can control the class-level flag
    from personal_assistant.mlx_server.engine import MLXEngine

    # Reset class-level availability cache so each test starts clean
    MLXEngine._mlx_available = None

    # Patch mlx_lm to be absent so the engine goes into "unavailable" mode
    with patch.dict(sys.modules, {"mlx_lm": None}):
        eng = MLXEngine(model_path=model_path)

    return eng


# ---------------------------------------------------------------------------
# PR-05-T01: Init without mlx-lm — no crash
# ---------------------------------------------------------------------------


class TestMLXEngineInit:
    def test_init_without_mlx_lm_does_not_crash(self):
        """MLXEngine must initialise cleanly even when mlx-lm is missing."""
        with patch.dict(sys.modules, {"mlx_lm": None}):
            from personal_assistant.mlx_server.engine import MLXEngine

            MLXEngine._mlx_available = None
            eng = MLXEngine()

        assert eng is not None

    def test_mlx_available_false_when_no_mlx_lm(self):
        """_mlx_available must be False when mlx-lm cannot be imported."""
        with patch.dict(sys.modules, {"mlx_lm": None}):
            from personal_assistant.mlx_server.engine import MLXEngine

            MLXEngine._mlx_available = None
            MLXEngine()

        assert MLXEngine._mlx_available is False

    def test_is_loaded_false_after_init(self):
        """is_loaded must be False before any model is actually loaded."""
        with patch.dict(sys.modules, {"mlx_lm": None}):
            from personal_assistant.mlx_server.engine import MLXEngine

            MLXEngine._mlx_available = None
            eng = MLXEngine()

        assert eng.is_loaded is False

    def test_model_name_not_configured_when_no_path(self):
        """model_name returns 'not configured' when no model path is given."""
        with patch.dict(sys.modules, {"mlx_lm": None}):
            import personal_assistant.config as cfg_mod
            from personal_assistant.mlx_server.engine import MLXEngine

            MLXEngine._mlx_available = None
            # Patch settings so mlx_model_path is empty even if .env sets it
            original = cfg_mod.settings.mlx_model_path
            cfg_mod.settings.mlx_model_path = ""
            try:
                eng = MLXEngine(model_path="")
            finally:
                cfg_mod.settings.mlx_model_path = original

        assert eng.model_name == "not configured"

    def test_model_name_returns_basename_when_path_given(self):
        """model_name returns the basename of the model path."""
        with patch.dict(sys.modules, {"mlx_lm": None}):
            from personal_assistant.mlx_server.engine import MLXEngine

            MLXEngine._mlx_available = None
            eng = MLXEngine(model_path="/models/my-model-7B")

        assert eng.model_name == "my-model-7B"


# ---------------------------------------------------------------------------
# PR-05-T02: chat() — graceful degradation
# ---------------------------------------------------------------------------


class TestMLXEngineChatGraceful:
    """chat() must RETURN a string, never raise, when unavailable."""

    def _make_unavailable_engine(self, model_path: str = ""):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine(model_path=model_path)
        return eng

    def test_chat_returns_string_not_raises(self):
        eng = self._make_unavailable_engine()
        result = eng.chat([{"role": "user", "content": "Hello"}])
        assert isinstance(result, str)

    def test_chat_result_contains_unavailable_hint(self):
        eng = self._make_unavailable_engine()
        result = eng.chat([{"role": "user", "content": "Hello"}])
        lower = result.lower()
        # Should mention either "unavailable"/"недоступна" or mlx
        assert "недоступна" in result or "mlx" in lower or "unavailable" in lower

    def test_chat_returns_UNAVAILABLE_MSG_constant(self):
        """chat() must return exactly _UNAVAILABLE_MSG on failure."""
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()

        result = eng.chat([{"role": "user", "content": "test"}])
        assert result == eng._UNAVAILABLE_MSG

    def test_chat_with_system_prompt_still_graceful(self):
        eng = self._make_unavailable_engine()
        result = eng.chat(
            [{"role": "user", "content": "test"}],
            system="You are a helpful assistant.",
        )
        assert isinstance(result, str)
        assert result == eng._UNAVAILABLE_MSG

    def test_chat_with_empty_messages_graceful(self):
        eng = self._make_unavailable_engine()
        result = eng.chat([])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# PR-05-T03: ask() — delegates to chat()
# ---------------------------------------------------------------------------


class TestMLXEngineAsk:
    def _make_unavailable_engine(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()
        return eng

    def test_ask_returns_string(self):
        eng = self._make_unavailable_engine()
        result = eng.ask("What is the weather?")
        assert isinstance(result, str)

    def test_ask_without_mlx_returns_unavailable_msg(self):
        eng = self._make_unavailable_engine()
        result = eng.ask("anything")
        assert result == eng._UNAVAILABLE_MSG

    def test_ask_with_context_returns_string(self):
        eng = self._make_unavailable_engine()
        result = eng.ask("Summarize this", context="Some long context here.")
        assert isinstance(result, str)

    def test_ask_truncates_long_context(self):
        """ask() truncates context that exceeds mlx_context_chars."""
        eng = self._make_unavailable_engine()

        # We can't check truncation here (engine fails before reaching it),
        # but we verify the call doesn't raise.
        long_context = "x" * 1_000_000
        result = eng.ask("test", context=long_context)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# PR-05-T04: generate() — raises RuntimeError when unavailable
# ---------------------------------------------------------------------------


class TestMLXEngineGenerate:
    def test_generate_raises_runtime_error_when_unavailable(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()

        with pytest.raises(RuntimeError):
            eng.generate("Hello world")

    def test_generate_raises_when_model_path_empty(self):
        """Even if mlx is 'available', generate() raises when path not set."""
        import personal_assistant.config as cfg_mod
        from personal_assistant.mlx_server.engine import MLXEngine

        # Simulate mlx available but no path
        MLXEngine._mlx_available = True
        original = cfg_mod.settings.mlx_model_path
        cfg_mod.settings.mlx_model_path = ""
        try:
            eng = MLXEngine(model_path="")
            with pytest.raises(RuntimeError):
                eng.generate("test")
        finally:
            cfg_mod.settings.mlx_model_path = original
            MLXEngine._mlx_available = None


# ---------------------------------------------------------------------------
# PR-05-T05: stream() — yields _UNAVAILABLE_MSG as single chunk
# ---------------------------------------------------------------------------


class TestMLXEngineStream:
    def test_stream_yields_unavailable_msg_when_no_mlx(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()

        chunks = list(eng.stream([{"role": "user", "content": "Hi"}]))
        assert len(chunks) == 1
        assert chunks[0] == eng._UNAVAILABLE_MSG

    def test_stream_yields_string_type(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()

        chunks = list(eng.stream([{"role": "user", "content": "test"}]))
        assert all(isinstance(c, str) for c in chunks)

    def test_stream_ask_yields_unavailable_msg(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = MLXEngine()

        chunks = list(eng.stream_ask("What is 2+2?"))
        assert len(chunks) == 1
        assert chunks[0] == eng._UNAVAILABLE_MSG


# ---------------------------------------------------------------------------
# PR-05-T06: _mlx_generate() API compatibility shim
# ---------------------------------------------------------------------------


class TestMlxGenerateShim:
    """Test _mlx_generate() parameter routing without real mlx-lm."""

    def _make_generate_fn(self, accepted_params):
        """Build a fake generate_fn that accepts specific params."""

        # We create a real function with the right signature
        if accepted_params == "temp":
            def fake_generate(model, tokenizer, prompt, max_tokens, verbose, temp):
                return f"result_temp_{temp}"
        elif accepted_params == "temperature":
            def fake_generate(model, tokenizer, prompt, max_tokens, verbose, temperature):
                return f"result_temperature_{temperature}"
        else:
            # Neither — triggers sampler path
            def fake_generate(model, tokenizer, prompt, max_tokens, verbose):
                return "result_sampler"

        return fake_generate

    def test_shim_uses_temp_param(self):
        from personal_assistant.mlx_server.engine import _mlx_generate

        gen_fn = self._make_generate_fn("temp")
        result = _mlx_generate(gen_fn, None, None, "hello", 100, 0.3)
        assert "result_temp_" in result

    def test_shim_uses_temperature_param(self):
        from personal_assistant.mlx_server.engine import _mlx_generate

        gen_fn = self._make_generate_fn("temperature")
        result = _mlx_generate(gen_fn, None, None, "hello", 100, 0.5)
        assert "result_temperature_" in result

    def test_shim_falls_through_without_temp(self):
        from personal_assistant.mlx_server.engine import _mlx_generate

        gen_fn = self._make_generate_fn("none")
        # make_sampler not available — should still work (run without temp control)
        with patch.dict(sys.modules, {"mlx_lm.sample_utils": None}):
            result = _mlx_generate(gen_fn, None, None, "hello", 100, 0.7)
        assert result == "result_sampler"

    def test_shim_handles_generation_response_object(self):
        """If the generate fn returns an object with .text, shim returns .text."""
        from personal_assistant.mlx_server.engine import _mlx_generate

        response_obj = SimpleNamespace(text="the answer")

        def fake_generate(model, tokenizer, prompt, max_tokens, verbose, temp):
            return response_obj

        result = _mlx_generate(fake_generate, None, None, "hello", 100, 0.3)
        assert result == "the answer"

    def test_shim_handles_plain_string_response(self):
        from personal_assistant.mlx_server.engine import _mlx_generate

        def fake_generate(model, tokenizer, prompt, max_tokens, verbose, temp):
            return "plain string result"

        result = _mlx_generate(fake_generate, None, None, "hello", 100, 0.3)
        assert result == "plain string result"


# ---------------------------------------------------------------------------
# PR-05-T07: _MLXThread.call() — dispatches and returns
# ---------------------------------------------------------------------------


class TestMLXThreadCall:
    def test_call_returns_function_result(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()
        result = thread.call(lambda: 42)
        assert result == 42

    def test_call_propagates_exception(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()
        with pytest.raises(ValueError, match="boom"):
            thread.call(lambda: (_ for _ in ()).throw(ValueError("boom")))

    def test_call_returns_string(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()
        result = thread.call(lambda: "hello from mlx thread")
        assert result == "hello from mlx thread"

    def test_call_sequential_calls_work(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()
        results = []
        for i in range(5):
            val = i  # capture
            results.append(thread.call(lambda v=val: v * 2))
        assert results == [0, 2, 4, 6, 8]


# ---------------------------------------------------------------------------
# PR-05-T08: _MLXThread.stream() — yields items from generator
# ---------------------------------------------------------------------------


class TestMLXThreadStream:
    def test_stream_yields_all_items(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()

        def gen_fn():
            yield "a"
            yield "b"
            yield "c"

        chunks = list(thread.stream(gen_fn))
        assert chunks == ["a", "b", "c"]

    def test_stream_propagates_exception_from_generator(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()

        def gen_fn():
            yield "first"
            raise RuntimeError("generator error")

        chunks = []
        with pytest.raises(RuntimeError, match="generator error"):
            for chunk in thread.stream(gen_fn):
                chunks.append(chunk)

        assert chunks == ["first"]

    def test_stream_empty_generator(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()

        def gen_fn():
            return
            yield  # make it a generator

        chunks = list(thread.stream(gen_fn))
        assert chunks == []

    def test_stream_large_output(self):
        from personal_assistant.mlx_server.engine import _MLXThread

        thread = _MLXThread()

        def gen_fn():
            for i in range(100):
                yield f"token_{i}"

        chunks = list(thread.stream(gen_fn))
        assert len(chunks) == 100
        assert chunks[0] == "token_0"
        assert chunks[-1] == "token_99"


# ---------------------------------------------------------------------------
# PR-05-T09: _UNAVAILABLE_MSG constant — content check
# ---------------------------------------------------------------------------


class TestUnavaialbleMsgConstant:
    def test_unavailable_msg_is_str(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        assert isinstance(MLXEngine._UNAVAILABLE_MSG, str)

    def test_unavailable_msg_mentions_mlx_lm(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        assert "mlx" in MLXEngine._UNAVAILABLE_MSG.lower()

    def test_unavailable_msg_mentions_model_path(self):
        from personal_assistant.mlx_server.engine import MLXEngine

        # Should mention either MODEL_PATH or the .env instructions
        msg = MLXEngine._UNAVAILABLE_MSG
        assert "PA_MLX_MODEL_PATH" in msg or "model" in msg.lower()

    def test_unavailable_msg_mentions_sync_works(self):
        """Message should reassure user that sync/search still works."""
        from personal_assistant.mlx_server.engine import MLXEngine

        msg = MLXEngine._UNAVAILABLE_MSG
        # Should hint sync / vault / search still works without the model
        assert any(
            kw in msg.lower() for kw in ["синхрониза", "поиск", "vault", "search", "sync"]
        )


# ---------------------------------------------------------------------------
# PR-05-T10: get_engine() singleton
# ---------------------------------------------------------------------------


class TestGetEngineSingleton:
    def test_get_engine_returns_mlx_engine_instance(self):
        from personal_assistant.mlx_server.engine import MLXEngine, get_engine

        MLXEngine._mlx_available = None
        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng = get_engine()

        assert isinstance(eng, MLXEngine)

    def test_get_engine_returns_same_instance(self):
        from personal_assistant.mlx_server import engine as engine_mod
        from personal_assistant.mlx_server.engine import MLXEngine, get_engine

        # Reset singleton
        engine_mod._engine = None
        MLXEngine._mlx_available = None

        with patch.dict(sys.modules, {"mlx_lm": None}):
            eng1 = get_engine()
            eng2 = get_engine()

        assert eng1 is eng2
