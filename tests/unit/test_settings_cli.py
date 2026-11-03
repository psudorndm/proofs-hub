from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proof_index.cli import app
from proof_index.errors import ConfigError
from proof_index.settings import load_settings, parse_dotenv, redact_database_url

runner = CliRunner()


def test_defaults_without_environment(tmp_path: Path) -> None:
    settings = load_settings(environ={}, dotenv_path=tmp_path / "missing.env")
    assert settings.database_url is None
    assert settings.blob_backend == "local"
    assert settings.public_base_path == "/"
    assert settings.missing() == ["DATABASE_URL"]


def test_environment_overrides_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_URL=postgresql+psycopg://a:b@h/db\nPUBLIC_BASE_PATH=/x/\n")
    settings = load_settings(environ={"PUBLIC_BASE_PATH": "/proof-papers/"}, dotenv_path=dotenv)
    assert settings.database_url == "postgresql+psycopg://a:b@h/db"
    assert settings.public_base_path == "/proof-papers/"


def test_empty_values_are_unset(tmp_path: Path) -> None:
    settings = load_settings(environ={"S3_BUCKET": "", "DATABASE_URL": "  "}, dotenv_path=None)
    assert settings.s3_bucket is None
    assert settings.database_url is None


def test_require_database_url_names_the_variable() -> None:
    settings = load_settings(environ={}, dotenv_path=None)
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        settings.require_database_url()


@pytest.mark.parametrize("bad", ["proof-papers/", "/proof-papers", "/a//b/", "/../"])
def test_invalid_base_path_is_a_config_error(bad: str) -> None:
    with pytest.raises(ConfigError, match="PUBLIC_BASE_PATH"):
        load_settings(environ={"PUBLIC_BASE_PATH": bad}, dotenv_path=None)


def test_s3_backend_requires_bucket() -> None:
    settings = load_settings(environ={"BLOB_BACKEND": "s3"}, dotenv_path=None)
    assert "S3_BUCKET" in settings.missing()
    with pytest.raises(ConfigError, match="S3_BUCKET"):
        settings.require_s3()


def test_parse_dotenv_handles_comments_quotes_and_export() -> None:
    parsed = parse_dotenv("# c\n\nexport A=\"1\"\nB='two'\nC=3 \nnot a line\n")
    assert parsed == {"A": "1", "B": "two", "C": "3"}


def test_redaction_hides_password() -> None:
    redacted = redact_database_url("postgresql+psycopg://proofs:s3cret@127.0.0.1:5432/proofs")
    assert "s3cret" not in redacted
    assert "proofs" in redacted


def test_cli_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_doctor_json_never_prints_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://proofs:hunter2@127.0.0.1:1/proofs")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    assert "hunter2" not in result.output
    summary = json.loads(result.output)
    assert summary["missing_settings"] == []
    assert summary["checks"] == {}


def test_doctor_database_failure_exit_code_and_no_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # Port 1 on localhost is closed; the connection fails quickly.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://proofs:hunter2@127.0.0.1:1/proofs")
    result = runner.invoke(app, ["doctor", "--check-db"])
    assert result.exit_code == 6
    assert "hunter2" not in result.output


def test_doctor_check_db_without_url_is_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["doctor", "--check-db"])
    assert result.exit_code == 2


def test_package_import_makes_no_network_calls() -> None:
    # Run in a fresh interpreter with socket creation disabled before import.
    code = (
        "import socket\n"
        "def _deny(*a, **k):\n"
        "    raise RuntimeError('network access during import')\n"
        "socket.socket = _deny\n"
        "socket.create_connection = _deny\n"
        "import proof_index, proof_index.cli, proof_index.settings, proof_index.clock\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
    assert socket.socket is not None
