"""Version reporting must stay in sync with pyproject.toml.

Regression anchor: the CLI previously hardcoded version="0.3.0" in a
@click.version_option, which drifted behind pyproject (0.5.0) so `--version` lied.
"""
import re
from pathlib import Path

from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.group import resolve_version

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject_version():
    for line in _PYPROJECT.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError("no version in pyproject.toml")


def test_resolve_version_matches_pyproject():
    assert resolve_version() == _pyproject_version()


def test_cli_version_flag_reports_pyproject_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert _pyproject_version() in result.output


def test_version_looks_like_semver():
    assert re.match(r"^\d+\.\d+\.\d+", resolve_version())
