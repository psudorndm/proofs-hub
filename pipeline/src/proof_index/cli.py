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
