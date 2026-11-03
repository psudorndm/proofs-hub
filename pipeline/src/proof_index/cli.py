"""``proof-index`` command-line entry point (spec section 4).

Commands are added as their task becomes implementable. Each command converts
``ProofIndexError`` into the documented exit code and never prints secrets.
"""

from __future__ import annotations

import functools
import json
import platform
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from proof_index import __version__
from proof_index.errors import ProofIndexError, StorageError
from proof_index.settings import Settings, load_settings, redact_database_url

app = typer.Typer(
    name="proof-index",
    help="Cryptographic Proofs Research Index pipeline.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def handle_errors[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Map pipeline errors onto exit codes with a one-line sanitized message."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except ProofIndexError as exc:
            typer.echo(f"error[{exc.code}]: {exc}", err=True)
            raise typer.Exit(code=exc.exit_code) from None

    return wrapper


def emit(summary: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(summary, sort_keys=True, indent=2, default=str))


def _tool_version(name: str, args: list[str]) -> str | None:
    path = shutil.which(name)
    if path is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [path, *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "present (version check failed)"
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else "present"


def check_database(settings: Settings) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    url = settings.require_database_url()
    engine = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=False)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("select current_setting('server_version')")).scalar_one()
    except Exception as exc:  # noqa: BLE001 - report class only, never the DSN
        raise StorageError(
            f"database connection to {redact_database_url(url)} failed ({type(exc).__name__})"
        ) from None
    finally:
        engine.dispose()
    return {"ok": True, "server_version": str(version)}


@app.command()
@handle_errors
def doctor(
    check_db: bool = typer.Option(False, "--check-db", help="Also connect to DATABASE_URL."),
    as_json: bool = typer.Option(False, "--json", help="Print a machine-readable summary."),
) -> None:
    """Report runtimes, configuration, and requested connection checks (no passwords)."""
    settings = load_settings()
    summary: dict[str, Any] = {
        "proof_index_version": __version__,
        "python": platform.python_version(),
        "tools": {
            "uv": _tool_version("uv", ["--version"]),
            "node": _tool_version("node", ["--version"]),
            "npm": _tool_version("npm", ["--version"]),
            "docker": _tool_version("docker", ["--version"]),
        },
        "settings": {
            "database_url": redact_database_url(settings.database_url)
            if settings.database_url
            else None,
            "blob_backend": settings.blob_backend,
            "local_blob_root": str(settings.local_blob_root),
            "export_root": str(settings.export_root),
            "source_config": str(settings.source_config),
            "site_config": str(settings.site_config),
            "public_site_url": settings.public_site_url,
            "public_base_path": settings.public_base_path,
            "s3_bucket_set": bool(settings.s3_bucket),
            "project_contact_set": bool(settings.project_contact),
        },
        "missing_settings": settings.missing(),
        "checks": {},
    }
    if check_db:
        summary["checks"]["database"] = check_database(settings)

    if as_json:
        emit(summary, True)
        return
    typer.echo(f"proof-index {__version__} on Python {summary['python']}")
    for name, version in summary["tools"].items():
        typer.echo(f"  {name:<7} {version or 'not found'}")
    typer.echo("settings:")
    for key, value in summary["settings"].items():
        typer.echo(f"  {key:<18} {value}")
    if summary["missing_settings"]:
        typer.echo("missing: " + ", ".join(summary["missing_settings"]))
    for name, result in summary["checks"].items():
        typer.echo(f"check {name}: ok ({result.get('server_version', '')})")


sources_app = typer.Typer(
    help="Source configuration and bounded verification.", no_args_is_help=True
)
app.add_typer(sources_app, name="sources")


@sources_app.command("verify")
@handle_errors
def sources_verify(
    source: str = typer.Option(..., "--source", help="eprint or arxiv"),
    bounded: bool = typer.Option(
        False, "--bounded", help="Required: confirms this is a small bounded check."
    ),
    out: Path = typer.Option(Path("work/verification"), "--out", help="Report directory."),
    capture_fixtures: Path | None = typer.Option(
        None, "--capture-fixtures", help="Also save raw responses with provenance here."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run a bounded live check of one provider. Never writes to the catalog."""
    from proof_index.clock import SystemClock
    from proof_index.config.sources import load_sources
    from proof_index.errors import InputError, ProviderError
    from proof_index.verify import run_verification

    if not bounded:
        raise InputError("pass --bounded: verification is deliberately a small sample")
    settings = load_settings()
    config = load_sources(settings.source_config)
    report = run_verification(
        source,
        config,
        SystemClock(),
        user_agent=config.render_user_agent(settings.project_contact),
        out_dir=out,
        capture_dir=capture_fixtures,
    )
    if as_json:
        emit(report, True)
    else:
        for step in report["steps"]:
            status = "ok" if step["ok"] else f"FAILED ({step['error']})"
            typer.echo(f"{step['step']:<55} {status}")
        typer.echo(f"report: {report['report_path']}")
    if not report["all_steps_ok"]:
        raise ProviderError(f"verification of {source} did not fully succeed; see report")


def _run_streams(
    source: str,
    kinds: tuple[str, ...],
    stream: str | None,
    mode: str,
    max_minutes: float | None,
    resume: bool | None,
) -> None:
    from proof_index.http.rate_limit import Budget
    from proof_index.ingest.service import (
        Pipeline,
        has_resumable_run,
        overall_exit,
        select_streams,
    )

    pipeline = Pipeline.from_settings(load_settings())
    keys = select_streams(pipeline.config, source, kinds, stream)
    if resume is False:
        busy = [k for k in keys if has_resumable_run(pipeline.engine, k)]
        if busy:
            from proof_index.errors import InputError

            raise InputError(f"unfinished runs exist for {', '.join(busy)}; pass --resume")
    budget = Budget(max_minutes * 60 if max_minutes else None)
    runner = pipeline.runner()
    summaries = []
    for key in keys:
        summary = runner.run_stream(key, mode, budget)
        summaries.append(summary)
        if summary.exit_code == 5:
            break
    emit(
        {
            "command": mode,
            "source": source,
            "complete": all(s.complete for s in summaries),
            "streams": [s.to_json() for s in summaries],
        },
        True,
    )
    code = overall_exit(summaries)
    if code:
        raise typer.Exit(code=code)


@app.command()
@handle_errors
def sync(
    source: str = typer.Option(..., "--source"),
    stream: str | None = typer.Option(None, "--stream", help="Only this configured stream."),
    max_minutes: float | None = typer.Option(None, "--max-minutes"),
) -> None:
    """Daily incremental/supplemental sync for one source (resumes unfinished runs)."""
    _run_streams(source, ("incremental", "supplemental"), stream, "sync", max_minutes, None)


@app.command()
@handle_errors
def backfill(
    source: str = typer.Option(..., "--source"),
    stream: str | None = typer.Option(None, "--stream"),
    resume: bool = typer.Option(False, "--resume", help="Continue an unfinished run."),
    max_minutes: float = typer.Option(
        45, "--max-minutes", help="Execution bound for this slice; never a coverage cutoff."
    ),
) -> None:
    """Resumable historical import. A bounded slice exits 0 with complete:false."""
    _run_streams(source, ("bootstrap", "supplemental"), stream, "backfill", max_minutes, resume)


@app.command()
@handle_errors
def classify(
    changed: bool = typer.Option(False, "--changed", help="Classify records lacking a row."),
    all_records: bool = typer.Option(False, "--all", help="Recompute every current record."),
    rules_version: str | None = typer.Option(None, "--rules-version"),
) -> None:
    """Classify current source records with the configured rules version."""
    from proof_index.classify.store import classify_current
    from proof_index.db.locks import CATALOG_WRITER, advisory_lock
    from proof_index.errors import InputError
    from proof_index.ingest.service import Pipeline, classification_hook

    if changed == all_records:
        raise InputError("pass exactly one of --changed or --all")
    pipeline = Pipeline.from_settings(load_settings())
    hook = classification_hook(pipeline)
    if all_records and rules_version is None:
        raise InputError("--all requires --rules-version")
    if rules_version is not None and rules_version != hook.classifier.version:
        raise InputError(
            f"--rules-version {rules_version} does not match the configured rules file "
            f"({hook.classifier.version})"
        )
    with advisory_lock(pipeline.engine, CATALOG_WRITER):
        report = classify_current(pipeline.engine, hook, only_missing=changed)
    emit(
        {
            "command": "classify",
            "rules_version": report.rules_version,
            "examined": report.examined,
            "classified": report.classified,
            "unchanged": report.unchanged,
            "decisions": report.decisions,
            "nondeterministic_mismatches": report.mismatched,
        },
        True,
    )
    if report.mismatched:
        from proof_index.errors import DataValidationError

        raise DataValidationError(f"{len(report.mismatched)} stored classifications differ")


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


def main() -> None:
    app(prog_name="proof-index")


if __name__ == "__main__":  # pragma: no cover
    main()
