#!/usr/bin/env python3
"""Performance baseline benchmark for pa-clean (§7 acceptance).

Measures three things:

  1. MLX model load time (cold start)
  2. MLX inference latency (first prompt + steady-state tokens/sec)
  3. Vault merge throughput (read+dedup+thread N synthetic messages)

Run on the target Mac:

    PA_MLX_MODEL_PATH=/path/to/model uv run python scripts/benchmark.py

Output: a markdown report on stdout — copy into docs/PERFORMANCE.md
if you want to keep the numbers.
"""

from __future__ import annotations

import gc
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Callable


def _section(title: str) -> None:
    print(f"\n## {title}\n")


def _row(label: str, value: str) -> None:
    print(f"- **{label}**: {value}")


def _time(fn: Callable[[], object]) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


# ---------------------------------------------------------------------------
# 1. MLX
# ---------------------------------------------------------------------------


def bench_mlx() -> None:
    _section("MLX inference")

    model_path = os.environ.get("PA_MLX_MODEL_PATH", "").strip()
    if not model_path:
        print("- _skipped: PA_MLX_MODEL_PATH not set_")
        return
    if not Path(model_path).exists():
        print(f"- _skipped: model path does not exist: {model_path}_")
        return

    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        print("- _skipped: mlx-lm not installed_")
        return

    _row("model_path", model_path)

    from personal_assistant.config import settings
    settings.mlx_model_path = model_path

    from personal_assistant.mlx_server.engine import MLXEngine

    MLXEngine._mlx_available = None
    engine = MLXEngine()

    load_time, _ = _time(engine._ensure_loaded)
    _row("cold load time", f"{load_time:.2f}s")

    # First inference (cold) — includes graph compilation
    prompt = "Скажи коротко: какая столица России?"
    t0 = time.perf_counter()
    resp = engine.chat([{"role": "user", "content": prompt}], max_tokens=64)
    cold_inf = time.perf_counter() - t0
    _row("cold inference (64 tok max)", f"{cold_inf:.2f}s — '{resp[:60]}…'")

    # Steady-state (3 short prompts)
    warmup_times: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter()
        engine.chat([{"role": "user", "content": "Привет"}], max_tokens=32)
        warmup_times.append(time.perf_counter() - t0)
    _row(
        "steady-state inference (32 tok, median of 3)",
        f"{statistics.median(warmup_times):.2f}s",
    )


# ---------------------------------------------------------------------------
# 2. Vault merge
# ---------------------------------------------------------------------------


def bench_vault_merge() -> None:
    _section("Vault merge (sync/dedup/threads)")

    import tempfile
    from datetime import datetime, timezone

    from personal_assistant.models import MailMessage
    from personal_assistant.sync.dedup_engine import DedupEngine
    from personal_assistant.sync.thread_tracker import ThreadTracker

    # Synthesise N messages spread across K threads.
    N = 1000
    K = 50

    msgs = []
    for i in range(N):
        thread_idx = i % K
        is_reply = (i // K) > 0
        prefix = "Re: " if is_reply else ""
        msgs.append(
            MailMessage(
                message_id=f"<msg-{i}@bench.example>",
                subject=f"{prefix}Thread #{thread_idx}",
                sender_name=f"User {thread_idx % 10}",
                sender_email=f"u{thread_idx % 10}@example.com",
                recipients=["me@example.com"],
                date=datetime(2026, 5, 1 + (i % 25), 10, i % 60, tzinfo=timezone.utc),
                mailbox="Inbox",
                body=f"Body of message #{i}.",
                source="outlook",
            )
        )
    _row("synthetic corpus", f"{N} messages × {K} threads")

    gc.collect()
    t0 = time.perf_counter()
    dedup = DedupEngine()
    deduped = dedup.dedup_messages(msgs)
    dedup_t = time.perf_counter() - t0
    _row("dedup", f"{dedup_t * 1000:.0f}ms ({len(deduped)} unique)")

    gc.collect()
    t0 = time.perf_counter()
    tracker = ThreadTracker()
    threaded = tracker.group_messages(deduped)
    thread_t = time.perf_counter() - t0
    thread_ids = {m.thread_id for m in threaded if m.thread_id}
    _row("thread grouping", f"{thread_t * 1000:.0f}ms ({len(thread_ids)} threads)")

    # Vault write — to temp dir
    from personal_assistant.vault.writer import VaultWriter

    with tempfile.TemporaryDirectory(prefix="pa-bench-") as tmp:
        vault = Path(tmp) / "vault"
        gc.collect()
        t0 = time.perf_counter()
        writer = VaultWriter(vault_root=vault)
        written = sum(1 for m in threaded if writer.write_message(m) is not None)
        write_t = time.perf_counter() - t0
        _row("vault write", f"{write_t * 1000:.0f}ms ({written} files)")

    _row("total merge pipeline", f"{(dedup_t + thread_t + write_t) * 1000:.0f}ms")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("# pa-clean — performance baseline")
    print(f"\n_generated: {datetime_now()}_")
    print(f"\n_python: {sys.version.split()[0]}_")
    print(f"\n_platform: {sys.platform}_")

    bench_vault_merge()
    bench_mlx()
    return 0


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


if __name__ == "__main__":
    sys.exit(main())
