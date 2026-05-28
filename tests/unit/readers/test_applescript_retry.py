"""
Unit tests for ``applescript_base.run_applescript`` retry/typed-error
behaviour — the B half of «A+B sync resilience».

Coverage:
  * transient TimeoutExpired → retried, eventually succeeds → returns stdout
  * permanent TimeoutExpired → AppleScriptTimeout after configured retries
  * TCC denial (1743) → AppleScriptPermissionDenied, no retry
  * Other non-zero exit → AppleScriptError, no retry
  * retries=0 → no retry on timeout
  * AppleScript* exceptions all subclass RuntimeError (back-compat)
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _ok_result(stdout: str = "ok"):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = 0
    r.stderr = ""
    return r


def _fail_result(stderr: str, returncode: int = 1):
    r = MagicMock()
    r.stdout = ""
    r.returncode = returncode
    r.stderr = stderr
    return r


# ----------------------------------------------------------------------
# Exception hierarchy
# ----------------------------------------------------------------------


def test_typed_exceptions_subclass_runtime_error():
    """Existing call sites use ``except RuntimeError`` — must keep working."""
    from personal_assistant.readers.applescript_base import (
        AppleScriptError,
        AppleScriptPermissionDenied,
        AppleScriptTimeout,
    )

    assert issubclass(AppleScriptError, RuntimeError)
    assert issubclass(AppleScriptTimeout, AppleScriptError)
    assert issubclass(AppleScriptPermissionDenied, AppleScriptError)


# ----------------------------------------------------------------------
# Retry on timeout
# ----------------------------------------------------------------------


def test_transient_timeout_is_retried_and_succeeds():
    """First call times out, second succeeds — overall return is the success."""
    from personal_assistant.readers import applescript_base

    side_effects = [
        subprocess.TimeoutExpired(cmd="osascript", timeout=1),
        _ok_result("recovered"),
    ]

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run", side_effect=side_effects
    ) as mock_run, patch.object(applescript_base, "RETRY_BACKOFF_SECONDS", (0.0,)):
        result = applescript_base.run_applescript("return 1", timeout=1, retries=1)

    assert result == "recovered"
    assert mock_run.call_count == 2


def test_permanent_timeout_raises_typed_exception_after_retries():
    from personal_assistant.readers import applescript_base

    timeout_side_effects = [
        subprocess.TimeoutExpired(cmd="osascript", timeout=1) for _ in range(5)
    ]

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run", side_effect=timeout_side_effects
    ) as mock_run, patch.object(applescript_base, "RETRY_BACKOFF_SECONDS", (0.0, 0.0)):
        with pytest.raises(applescript_base.AppleScriptTimeout, match="attempts=3"):
            applescript_base.run_applescript("return 1", timeout=1, retries=2)

    # 1 initial + 2 retries = 3 attempts
    assert mock_run.call_count == 3


def test_retries_zero_no_retry_on_timeout():
    from personal_assistant.readers import applescript_base

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=1),
    ) as mock_run:
        with pytest.raises(applescript_base.AppleScriptTimeout):
            applescript_base.run_applescript("x", timeout=1, retries=0)

    assert mock_run.call_count == 1


def test_retry_on_timeout_false_disables_retry():
    from personal_assistant.readers import applescript_base

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=1),
    ) as mock_run:
        with pytest.raises(applescript_base.AppleScriptTimeout):
            applescript_base.run_applescript(
                "x", timeout=1, retries=5, retry_on_timeout=False
            )

    # retry_on_timeout=False clamps attempts to 1 regardless of retries
    assert mock_run.call_count == 1


# ----------------------------------------------------------------------
# No retry on deterministic failures
# ----------------------------------------------------------------------


def test_tcc_permission_denied_is_not_retried():
    """Error 1743 means TCC denial — retrying does not help."""
    from personal_assistant.readers import applescript_base

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        return_value=_fail_result(
            "execution error: Not authorised to send Apple events to Mail. (1743)"
        ),
    ) as mock_run:
        with pytest.raises(applescript_base.AppleScriptPermissionDenied):
            applescript_base.run_applescript("x", timeout=1, retries=3)

    assert mock_run.call_count == 1


def test_other_nonzero_exit_is_not_retried():
    """Compile errors, syntax errors, etc. are deterministic — no retry."""
    from personal_assistant.readers import applescript_base

    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        return_value=_fail_result("syntax error: A end of line was found"),
    ) as mock_run:
        with pytest.raises(applescript_base.AppleScriptError):
            applescript_base.run_applescript("x", timeout=1, retries=3)

    assert mock_run.call_count == 1


def test_non_macos_raises_typed_error_without_subprocess_call():
    from personal_assistant.readers import applescript_base

    with patch("sys.platform", "linux"), patch("subprocess.run") as mock_run:
        with pytest.raises(applescript_base.AppleScriptError, match="macOS"):
            applescript_base.run_applescript("x")

    assert mock_run.call_count == 0
