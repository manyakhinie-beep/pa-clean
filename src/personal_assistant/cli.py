"""
CLI entry point for personal-assistant.

Commands:
  pa check           — check apps & permissions
  pa sync-calendar   — sync Apple Calendar → vault
  pa sync-mail       — sync Apple Mail → vault
  pa sync-all        — sync all configured sources
  pa status          — vault statistics
  pa serve           — start MLX FastAPI server
  pa run-tasks       — run MLX pipeline once (classify + summarize + digest)
  pa search QUERY    — BM25-поиск по vault с LLM-синтезом
  pa classify        — classify/tag vault documents
  pa build-index     — построить LanceDB векторный индекс (Stage M2)
  pa list-models     — рекомендуемые embedding-модели для гибридного поиска (Stage M2)
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger
from rich.console import Console
from rich.table import Table

from personal_assistant.config import settings

console = Console()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else settings.log_level
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


# ---------------------------------------------------------------------------
# MLX model config helpers
# ---------------------------------------------------------------------------

# Fields that mlx-lm validates as float but model packagers sometimes store as int.
_FLOAT_FIELDS = {
    "routed_scaling_factor",
    "scaling_factor",
    "rope_scaling_factor",
    "attention_factor",
    "beta_fast",
    "beta_slow",
    "mscale",
    "mscale_all_dim",
}


def _find_int_float_fields(config_path) -> list[str]:
    """Return list of field names that are int but should be float."""
    import json
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    bad = []
    for key, val in cfg.items():
        if key in _FLOAT_FIELDS and isinstance(val, int):
            bad.append(f"{key}={val!r}")
    # Also scan nested dicts (rope_scaling etc.)
    for key, val in cfg.items():
        if isinstance(val, dict):
            for k2, v2 in val.items():
                if k2 in _FLOAT_FIELDS and isinstance(v2, int):
                    bad.append(f"{key}.{k2}={v2!r}")
    return bad


def _fix_int_float_fields(config_path) -> list[str]:
    """Convert int→float for known float fields. Returns list of fixed keys."""
    import json
    p = Path(config_path)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    fixed = []

    for key in list(cfg.keys()):
        if key in _FLOAT_FIELDS and isinstance(cfg[key], int):
            cfg[key] = float(cfg[key])
            fixed.append(f"{key}: {int(cfg[key])} → {cfg[key]}")
        elif isinstance(cfg[key], dict):
            for k2 in list(cfg[key].keys()):
                if k2 in _FLOAT_FIELDS and isinstance(cfg[key][k2], int):
                    old = cfg[key][k2]
                    cfg[key][k2] = float(old)
                    fixed.append(f"{key}.{k2}: {old} → {cfg[key][k2]}")

    if fixed:
        # Write back with preserved formatting
        p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return fixed


def _merge_contacts(lists):
    """Merge multiple Contact lists by email (dedup & union sources)."""
    from personal_assistant.models import Contact

    merged: dict[str, Contact] = {}
    for contacts in lists:
        for c in contacts:
            email = c.email.strip().lower()
            if email not in merged:
                merged[email] = c.model_copy()
            else:
                existing = merged[email]
                if not existing.name and c.name:
                    existing.name = c.name
                for src in c.sources:
                    if src not in existing.sources:
                        existing.sources.append(src)
    return list(merged.values())


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--vault", default=None, type=click.Path(), help="Override vault path")
@click.option("--verbose", "-v", is_flag=True, help="Debug logging")
@click.pass_context
def main(ctx: click.Context, vault: str | None, verbose: bool) -> None:
    """Personal AI Assistant — Apple data sync to Obsidian vault."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["vault"] = Path(vault) if vault else settings.vault_path
    ctx.obj["overwrite"] = settings.overwrite


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@main.command("check")
@click.pass_context
def check(ctx: click.Context) -> None:
    """Check which apps and permissions are available on this machine."""
    from personal_assistant.readers.applescript_base import (
        is_app_installed,
        run_applescript,
    )

    vault_path: Path = ctx.obj["vault"]

    table = Table(title="System Check", show_header=True)
    table.add_column("Item", style="cyan")
    table.add_column("Status")

    def row(label: str, ok: bool, detail: str = "") -> None:
        status = "[green]✓ OK[/green]" if ok else "[red]✗ Missing[/red]"
        if detail:
            status += f"  [dim]{detail}[/dim]"
        table.add_row(label, status)

    # Apps
    row("Calendar.app", is_app_installed("Calendar"))
    row("Mail.app", is_app_installed("Mail"))

    # osascript sanity check
    try:
        run_applescript('return "ok"', timeout=5)
        row("osascript (shell)", True)
    except Exception as e:
        row("osascript (shell)", False, str(e))

    # Calendar permission
    try:
        run_applescript(
            'tell application "Calendar" to return name of first calendar', timeout=10
        )
        row("Calendar permission", True)
    except Exception:
        row(
            "Calendar permission",
            False,
            "Run 'pa sync-calendar' to trigger macOS prompt",
        )

    # Mail permission
    try:
        run_applescript(
            'tell application "Mail" to return name of first account', timeout=10
        )
        row("Mail permission", True)
    except Exception:
        row("Mail permission", False, "Run 'pa sync-mail' to trigger macOS prompt")

    # Vault
    row("Vault exists", vault_path.exists(), str(vault_path))

    # ── MLX / model ──────────────────────────────────────────────────────────
    import platform
    import subprocess as _sp

    table.add_section()

    def _is_apple_silicon() -> bool:
        try:
            out = _sp.check_output(
                ["sysctl", "-n", "hw.optional.arm64"],
                stderr=_sp.DEVNULL,
                timeout=2,
            ).decode().strip()
            return out == "1"
        except Exception:
            return platform.machine() == "arm64"

    is_arm = _is_apple_silicon()
    cpu_label = platform.machine()
    row(
        "Apple Silicon M-chip",
        is_arm,
        "" if is_arm else f"hw={cpu_label} — not M1/M2/M3/M4 (MLX not supported)",
    )

    py_machine = platform.machine()
    py_native = py_machine == "arm64"
    row(
        "Python binary arm64 (not Rosetta)",
        py_native or not is_arm,
        "" if (py_native or not is_arm) else (
            f"Python is {py_machine} (Rosetta) — run: rm -rf .venv && ./setup.sh"
        ),
    )

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    py_mlx_ok = sys.version_info < (3, 14)
    row(
        f"Python {py_ver} (MLX needs ≤3.13)",
        py_mlx_ok,
        "" if py_mlx_ok else (
            "MLX has no cp314 wheels — "
            "run: rm -rf .venv && uv venv --python 3.13 && ./setup.sh"
        ),
    )
    mlx_prereqs = is_arm and py_native and py_mlx_ok

    try:
        import importlib.metadata

        import mlx_lm  # noqa: F401
        mlx_ver = importlib.metadata.version("mlx-lm")
        row("mlx-lm installed", True, f"v{mlx_ver}")
        mlx_installed = True
    except ImportError:
        if not mlx_prereqs:
            hint = "requires Apple Silicon M-chip + native arm64 Python ≤ 3.13"
        elif not py_native:
            hint = "Python is Rosetta (x86_64) — run: rm -rf .venv && ./setup.sh"
        else:
            hint = "run: ./setup.sh  (mlx-lm installed via uv sync)"
        row("mlx-lm installed", False, hint)
        mlx_installed = False

    model_path_str = settings.mlx_model_path
    if not model_path_str:
        row("PA_MLX_MODEL_PATH", False, "not set in .env")
    else:
        model_path = Path(model_path_str)
        row(
            "PA_MLX_MODEL_PATH",
            model_path.exists(),
            str(model_path) + ("" if model_path.exists() else "  ← not found"),
        )

        if model_path.exists():
            cfg_path = model_path / "config.json"
            tok_path = model_path / "tokenizer.json"
            row(
                "Model config.json",
                cfg_path.exists(),
                "" if cfg_path.exists() else "missing — re-download model",
            )
            row(
                "Model tokenizer.json",
                tok_path.exists(),
                "" if tok_path.exists() else "missing — re-download model",
            )
            if cfg_path.exists():
                bad = _find_int_float_fields(cfg_path)
                if bad:
                    row(
                        "config.json int/float",
                        False,
                        f"{bad} — run: pa fix-model-config",
                    )
                else:
                    row("config.json int/float", True)

        if mlx_installed and mlx_prereqs and model_path.exists():
            with console.status("Loading model (first-time may take ~30 s)…"):
                try:
                    from mlx_lm import load as _mlx_load
                    # mlx_lm.load() returns (model, tokenizer) on some versions
                    # and (model, tokenizer, config) on others — absorb extras.
                    m, tok_obj, *_ = _mlx_load(str(model_path))
                    row("Model loads OK", True, model_path.name)
                    try:
                        from mlx_lm import generate as _mlx_gen

                        from personal_assistant.mlx_server.engine import _mlx_generate
                        out = _mlx_generate(_mlx_gen, m, tok_obj, "Hi", max_tokens=5, temp=0.0)
                        row("Inference smoke test", bool(out), repr(out[:40] if out else ""))
                    except Exception as e:
                        row("Inference smoke test", False, str(e)[:80])
                except Exception as e:
                    err = str(e)
                    hint = "  run: pa fix-model-config" if "expected float, got int" in err else ""
                    row("Model loads OK", False, err[:120] + hint)

    console.print(table)


# ---------------------------------------------------------------------------
# fix-model-config
# ---------------------------------------------------------------------------


@main.command("fix-model-config")
@click.option(
    "--model-path",
    default=None,
    type=click.Path(),
    help="Path to MLX model directory (defaults to PA_MLX_MODEL_PATH)",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would change without writing")
def fix_model_config(model_path, dry_run):
    """
    Fix int/float type mismatches in an MLX model's config.json.

    Some models are packaged with integer values (e.g. routed_scaling_factor=1)
    where mlx-lm expects floats (1.0).  This command converts them in-place.

    Example error this fixes:
      TypeError: Field 'routed_scaling_factor' expected float, got int
    """
    path_str = model_path or settings.mlx_model_path
    if not path_str:
        console.print("[red]No model path — set PA_MLX_MODEL_PATH in .env or pass --model-path[/red]")
        raise SystemExit(1)

    cfg_path = Path(path_str) / "config.json"
    if not cfg_path.exists():
        console.print(f"[red]config.json not found: {cfg_path}[/red]")
        raise SystemExit(1)

    bad = _find_int_float_fields(cfg_path)
    if not bad:
        console.print("[green]✓ config.json looks fine — no int/float issues found.[/green]")
        return

    console.print(f"[yellow]Found {len(bad)} int field(s) that should be float:[/yellow]")
    for b in bad:
        console.print(f"  • {b}")

    if dry_run:
        console.print("[dim]--dry-run: no changes written.[/dim]")
        return

    fixed = _fix_int_float_fields(cfg_path)
    if fixed:
        console.print(f"\n[green]✓ Fixed {len(fixed)} field(s) in {cfg_path}:[/green]")
        for f in fixed:
            console.print(f"  • {f}")
        console.print("\n[dim]Re-run [bold]pa check[/bold] to verify model loads correctly.[/dim]")
    else:
        console.print("[yellow]Nothing changed.[/yellow]")


# ---------------------------------------------------------------------------
# sync-calendar (Calendar.app)
# ---------------------------------------------------------------------------


@main.command("sync-calendar")
@click.option("--days-back", default=None, type=int)
@click.option("--days-forward", default=None, type=int)
@click.option("--calendar", "calendar_names", multiple=True)
@click.option("--overwrite", is_flag=True, default=False)
@click.pass_context
def sync_calendar(ctx, days_back, days_forward, calendar_names, overwrite):
    """Sync Apple Calendar events to vault."""
    from personal_assistant.readers.calendar_reader import CalendarReader
    from personal_assistant.vault.writer import VaultWriter

    vault_path: Path = ctx.obj["vault"]
    _overwrite = overwrite or ctx.obj["overwrite"]

    reader = CalendarReader()
    reader.PER_CAL_TIMEOUT = settings.calendar_per_cal_timeout
    console.rule("[bold blue]Calendar.app Sync")
    with console.status("Fetching events…"):
        events = reader.fetch_events(
            days_back=days_back or settings.calendar_days_back,
            days_forward=days_forward or settings.calendar_days_forward,
            calendar_names=list(calendar_names) or settings.calendar_names_list or None,
            fetch_attendees=settings.calendar_fetch_attendees,
            max_events_per_calendar=settings.calendar_max_events,
        )
    contacts = reader.extract_contacts(events)

    writer = VaultWriter(vault_path)
    with console.status("Writing to vault…"):
        ew, es = writer.write_events(events, overwrite=_overwrite)
        cw, cs = writer.write_contacts(contacts, overwrite=_overwrite)

    _print_summary("Calendar.app", events=(ew, es), contacts=(cw, cs))


# ---------------------------------------------------------------------------
# sync-mail (Mail.app)
# ---------------------------------------------------------------------------


@main.command("sync-mail")
@click.option("--days-back", default=None, type=int)
@click.option("--overwrite", is_flag=True, default=False)
@click.pass_context
def sync_mail(ctx, days_back, overwrite):
    """Sync Apple Mail messages to vault."""
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.sync.thread_tracker import ThreadTracker
    from personal_assistant.vault.writer import VaultWriter

    vault_path: Path = ctx.obj["vault"]
    _overwrite = overwrite or ctx.obj["overwrite"]

    reader = MailReader()
    reader.PER_MBOX_TIMEOUT = settings.mail_per_mbox_timeout
    console.rule("[bold blue]Mail.app Sync")
    with console.status("Fetching messages…"):
        messages = reader.fetch_messages(
            days_back=days_back or settings.mail_days_back,
            max_messages_per_mailbox=settings.mail_max_messages,
            fetch_body=settings.mail_fetch_body,
            fetch_recipients=settings.mail_fetch_recipients,
        )
    contacts = reader.extract_contacts(messages)

    with console.status("Grouping threads…"):
        ThreadTracker().group_messages(messages)

    writer = VaultWriter(vault_path)
    with console.status("Writing to vault…"):
        mw, ms = writer.write_messages(messages, overwrite=_overwrite)
        cw, cs = writer.write_contacts(contacts, overwrite=_overwrite)

    _print_summary("Mail.app", messages=(mw, ms), contacts=(cw, cs))


# ---------------------------------------------------------------------------
# sync-all
# ---------------------------------------------------------------------------


@main.command("sync-all")
@click.option("--overwrite", is_flag=True, default=False)
@click.option(
    "--sources",
    default=None,
    help="Comma-separated sources to sync (overrides PA_SYNC_SOURCES). "
         "Available: calendar, mail",
)
@click.pass_context
def sync_all(ctx, overwrite, sources):
    """
    Sync all configured sources: Calendar.app and Mail.app.

    Sources are controlled by PA_SYNC_SOURCES in .env or the --sources flag.
    Thread-tracking is applied to mail messages.
    Contacts from all sources are merged and written to vault/contacts/.
    """
    from personal_assistant.readers.calendar_reader import CalendarReader
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.sync.thread_tracker import ThreadTracker
    from personal_assistant.vault.writer import VaultWriter

    vault_path: Path = ctx.obj["vault"]
    _overwrite = overwrite or ctx.obj["overwrite"]
    writer = VaultWriter(vault_path)

    # Resolve active sources
    if sources:
        active_sources = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        active_sources = settings.sync_sources_list or ["calendar", "mail"]

    console.print(f"[dim]Active sources: {', '.join(active_sources)}[/dim]")

    all_contacts = []

    # --- Calendar.app ---
    if "calendar" in active_sources:
        console.rule("[bold blue]Calendar.app")
        try:
            cal_reader = CalendarReader()
            cal_reader.PER_CAL_TIMEOUT = settings.calendar_per_cal_timeout
            with console.status("Fetching Calendar.app events…"):
                events = cal_reader.fetch_events(
                    days_back=settings.calendar_days_back,
                    days_forward=settings.calendar_days_forward,
                    calendar_names=settings.calendar_names_list or None,
                    fetch_attendees=settings.calendar_fetch_attendees,
                    max_events_per_calendar=settings.calendar_max_events,
                )
            all_contacts.append(cal_reader.extract_contacts(events))
            with console.status("Writing calendar events…"):
                ew, es = writer.write_events(events, overwrite=_overwrite)
            _print_summary("Calendar.app", events=(ew, es))
        except Exception as e:
            console.print(f"[red]Calendar.app error: {e}[/red]")

    # --- Mail.app ---
    if "mail" in active_sources:
        console.rule("[bold blue]Mail.app")
        try:
            mail_reader = MailReader()
            mail_reader.PER_MBOX_TIMEOUT = settings.mail_per_mbox_timeout
            with console.status("Fetching Mail.app messages…"):
                messages = mail_reader.fetch_messages(
                    days_back=settings.mail_days_back,
                    max_messages_per_mailbox=settings.mail_max_messages,
                    fetch_body=settings.mail_fetch_body,
                    fetch_recipients=settings.mail_fetch_recipients,
                )
            all_contacts.append(mail_reader.extract_contacts(messages))
            with console.status("Grouping threads…"):
                ThreadTracker().group_messages(messages)
            with console.status("Writing mail messages…"):
                mw, ms = writer.write_messages(messages, overwrite=_overwrite)
            _print_summary("Mail.app", messages=(mw, ms))
        except Exception as e:
            console.print(f"[red]Mail.app error: {e}[/red]")

    # --- Merged contacts ---
    console.rule("[bold blue]Contacts")
    merged = _merge_contacts(all_contacts)
    with console.status(f"Writing {len(merged)} merged contacts…"):
        cw, cs = writer.write_contacts(merged, overwrite=_overwrite)
    _print_summary("Contacts", contacts=(cw, cs))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@main.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show vault statistics."""
    vault_path: Path = ctx.obj["vault"]

    if not vault_path.exists():
        console.print(f"[yellow]Vault not found at {vault_path}[/yellow]")
        console.print("Run [bold]pa sync-all[/bold] to create it.")
        return

    table = Table(title=f"Vault: {vault_path}", show_header=True)
    table.add_column("Section", style="cyan")
    table.add_column("Files", justify="right")

    for section in ("calendar", "mail", "contacts", "threads", "attachments"):
        path = vault_path / section
        count = len(list(path.rglob("*.md"))) if path.exists() else 0
        table.add_row(section, str(count))

    console.print(table)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@main.command("serve")
@click.option("--host", default=None, help="Override host (default from config)")
@click.option(
    "--port", default=None, type=int, help="Override port (default from config)"
)
@click.option(
    "--reload", is_flag=True, default=False, help="Auto-reload on code changes (dev)"
)
@click.option(
    "--preload-model / --no-preload-model",
    default=True,
    show_default=True,
    help=(
        "Eagerly load the MLX model at server startup so the first chat request "
        "is instant instead of waiting 10–60 s for weights to load. "
        "Disable with --no-preload-model on machines without Apple Silicon."
    ),
)
@click.pass_context
def serve(ctx, host, port, reload, preload_model):
    """Start the MLX FastAPI server."""
    import os

    import uvicorn

    _host = host or settings.server_host
    _port = port or settings.server_port

    if preload_model:
        os.environ["PA_PRELOAD_MODEL"] = "1"

    model_label = settings.mlx_model_path or "not configured"
    preload_label = "[green]on[/green]" if preload_model else "[dim]off[/dim]"

    console.rule(f"[bold blue]Personal Assistant Server — {_host}:{_port}")
    console.print(f"  Model:   [cyan]{model_label}[/cyan]  (preload={preload_label})")
    console.print(f"  Vault:   [cyan]{settings.vault_path}[/cyan]")
    console.print(
        f"  Docs:    [link=http://{_host}:{_port}/docs]http://{_host}:{_port}/docs[/link]"
    )
    console.print()
    uvicorn.run(
        "personal_assistant.mlx_server.server:app",
        host=_host,
        port=_port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


# ---------------------------------------------------------------------------
# run-tasks
# ---------------------------------------------------------------------------


@main.command("run-tasks")
@click.pass_context
def run_tasks(ctx):
    """Run the MLX processing pipeline once (classify + summarize + daily digest)."""
    from personal_assistant.mlx_server.scheduler import run_pipeline

    vault_path: Path = ctx.obj["vault"]
    console.rule("[bold blue]MLX Pipeline")

    if not settings.mlx_model_path:
        console.print("[red]PA_MLX_MODEL_PATH is not set in .env[/red]")
        return

    with console.status("Running pipeline…"):
        report = run_pipeline(vault_path)

    classify = report.get("classify", {})
    summary = report.get("summary", {})

    table = Table(title="Pipeline Complete", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Result")
    table.add_row(
        "Classified", f"{classify.get('classified', 0)}/{classify.get('total', 0)} docs"
    )
    table.add_row(
        "Recent mail", f"{summary.get('recent_mail_count', 0)} emails summarized"
    )
    table.add_row("Digest", report.get("digest_path", "—"))
    console.print(table)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@main.command("search")
@click.argument("query")
@click.option(
    "--section", "-s", multiple=True, help="Limit to section: calendar, mail, contacts"
)
@click.option("--top-k", default=8, type=int)
@click.pass_context
def search_cmd(ctx, query, section, top_k):
    """Search vault with LLM synthesis."""
    from personal_assistant.mlx_server.engine import MLXEngine
    from personal_assistant.mlx_server.tasks.search import search as do_search
    from personal_assistant.mlx_server.vault_index import VaultIndex

    vault_path: Path = ctx.obj["vault"]

    if not settings.mlx_model_path:
        console.print("[red]PA_MLX_MODEL_PATH is not set in .env[/red]")
        return

    with console.status("Loading vault…"):
        index = VaultIndex(vault_path).load()
    with console.status("Searching…"):
        result = do_search(
            query=query,
            engine=MLXEngine(),
            index=index,
            sections=list(section) or None,
            top_k=top_k,
        )

    console.rule(f"[cyan]Search: {query}")
    console.print(result.answer)
    console.print(
        f"\n[dim]Sources ({result.doc_count}): {', '.join(result.source_titles[:5])}[/dim]"
    )


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


@main.command("classify")
@click.option("--section", "-s", multiple=True)
@click.option(
    "--no-write", is_flag=True, default=False, help="Dry run — don't update .md files"
)
@click.option(
    "--use-llm", is_flag=True, default=False, help="Use LLM for semantic classification"
)
@click.pass_context
def classify_cmd(ctx, section, no_write, use_llm):
    """Classify and tag vault documents using rules (+ optionally LLM)."""
    from personal_assistant.mlx_server.engine import MLXEngine
    from personal_assistant.mlx_server.tasks.classify import classify_vault
    from personal_assistant.mlx_server.vault_index import VaultIndex

    vault_path: Path = ctx.obj["vault"]

    with console.status("Loading vault…"):
        index = VaultIndex(vault_path).load()

    engine = MLXEngine() if use_llm else None
    sections = list(section) or None

    with console.status("Classifying…"):
        result = classify_vault(
            index=index,
            sections=sections,
            engine=engine,
            write_tags=not no_write,
        )

    table = Table(
        title=f"Classification {'(dry run)' if no_write else '(tags written)'}",
        show_header=True,
    )
    table.add_column("Classifier", style="cyan")
    table.add_column("Label")
    table.add_column("Count", justify="right")

    for classifier, counts in result.label_counts.items():
        for label, count in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(classifier, label, str(count))

    console.print(f"Total: {result.total} docs, classified: {result.classified}")
    console.print(table)


# ---------------------------------------------------------------------------
# list-models (Stage M2)
# ---------------------------------------------------------------------------


@main.command("list-models")
def list_models_cmd():
    """
    Показать рекомендуемые embedding-модели для гибридного поиска (Stage M2).

    Используйте имя из этого списка в PA_EMBEDDING_MODEL в .env.
    Текущая модель читается из настроек (PA_EMBEDDING_MODEL / PA_EMBEDDING_MODEL_PATH).
    """
    from personal_assistant.mlx_server.vector_index import RECOMMENDED_MODELS

    console.rule("[bold blue]Рекомендуемые embedding-модели (sentence-transformers)")

    current_model = settings.embedding_model or ""
    current_path = settings.embedding_model_path or ""

    if current_path:
        console.print(f"Активная модель:   [cyan]local path → {current_path}[/cyan]")
    elif current_model:
        console.print(f"Активная модель:   [cyan]{current_model}[/cyan]")
    else:
        console.print(
            "[yellow]Модель не задана.[/yellow] Добавьте PA_EMBEDDING_MODEL= в .env"
        )
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Модель", style="cyan", no_wrap=True)
    table.add_column("dim", justify="right")
    table.add_column("Размер", justify="right")
    table.add_column("Языки", style="dim")
    table.add_column("Примечание")

    for m in RECOMMENDED_MODELS:
        name = m.get("model", "?")
        dim = str(m.get("dim", "?"))
        size_gb = m.get("size_gb", None)
        size_str = f"{size_gb:.2f} GB" if size_gb else "?"
        langs = m.get("languages", "")
        note = m.get("note", "")
        marker = " [green]✓[/green]" if name == current_model else ""
        table.add_row(name + marker, dim, size_str, langs, note)

    console.print(table)
    console.print()
    console.print("Для скачивания и использования — добавьте в [bold].env[/bold]:")
    console.print("  [cyan]PA_EMBEDDING_MODEL=BAAI/bge-m3[/cyan]")
    console.print()
    console.print("Для использования локальной модели с диска:")
    console.print("  [cyan]PA_EMBEDDING_MODEL_PATH=~/models/bge-m3[/cyan]")
    console.print()
    console.print("Затем запустите: [bold]uv run pa build-index[/bold]")


# ---------------------------------------------------------------------------
# build-index (Stage M2)
# ---------------------------------------------------------------------------


@main.command("build-index")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Пересоздать индекс даже если уже существует",
)
@click.option(
    "--batch-size",
    default=32,
    type=int,
    show_default=True,
    help="Размер батча для embedding",
)
@click.pass_context
def build_index(ctx, force, batch_size):
    """
    Построить LanceDB векторный индекс для гибридного поиска (Stage M2).

    Использует BAAI/bge-m3 (multilingual, ~570 MB).
    Модель скачивается автоматически при первом запуске.
    Индекс сохраняется в vault/.vector_db/ (постоянно).

    После построения команда `pa search` и эндпоинт /search/hybrid
    будут использовать гибридный BM25 + векторный поиск.
    """
    from personal_assistant.mlx_server.vault_index import VaultIndex
    from personal_assistant.mlx_server.vector_index import VectorIndex

    vault_path: Path = ctx.obj["vault"]

    console.rule("[bold blue]Stage M2: Построение векторного индекса")

    with console.status("Загрузка vault…"):
        index = VaultIndex(vault_path).load()

    total_docs = len(index.docs)
    if total_docs == 0:
        console.print("[yellow]Vault пуст — запустите pa sync-all[/yellow]")
        return

    vi = VectorIndex(vault_path)

    if vi.is_built() and not force:
        stats = vi.stats
        console.print(
            f"[green]Векторный индекс уже построен: {stats['docs']} docs[/green]\n"
            f"[dim]Используйте --force для пересоздания[/dim]"
        )
        return

    console.print(f"Документов для индексации: [bold]{total_docs}[/bold]")
    console.print("Модель: [cyan]BAAI/bge-m3[/cyan] (multilingual, dim=1024)")
    console.print(f"Хранилище: [cyan]{vi.db_path}[/cyan]")
    console.print()
    console.print("[dim]Первый запуск: ~570 MB скачается автоматически[/dim]")
    console.print("[dim]Время индексации: ~1-5 мин на 1000 документов[/dim]")
    console.print()

    with console.status(
        f"Генерация embeddings и построение индекса ({total_docs} docs)…"
    ):
        count = vi.build(index.docs, batch_size=batch_size)

    table = Table(title="Векторный индекс построен ✓", show_header=True)
    table.add_column("Параметр", style="cyan")
    table.add_column("Значение")
    table.add_row("Проиндексировано", str(count))
    table.add_row("Модель", "BAAI/bge-m3 (dim=1024)")
    table.add_row("Хранилище", str(vi.db_path))
    table.add_row("Поиск", "pa search / POST /search/hybrid")
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_summary(source: str, **kwargs) -> None:
    """kwargs: section_name=(written, skipped)"""
    table = Table(title=f"{source} — done", show_header=True, header_style="bold")
    table.add_column("Item", style="cyan")
    table.add_column("Written", justify="right", style="green")
    table.add_column("Skipped", justify="right", style="dim")
    for key, (w, s) in kwargs.items():
        table.add_row(key.replace("_", " ").title(), str(w), str(s))
    console.print(table)
