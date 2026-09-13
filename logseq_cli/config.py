"""Configuration file loading.

The CLI works without a config file: everything here is optional, and every
value can still be overridden by a flag or an environment variable. What the
file carries is the knowledge the code cannot have — how *your* graph marks
projects and people, what your journal sections are called.

No graph-specific value has a built-in default. A query that needs one says so
and exits non-zero rather than returning an empty result, because an empty
result is indistinguishable from "nothing matched" and that is exactly the kind
of silent failure the rest of this CLI exists to avoid.

Precedence, highest first: command-line flag, environment variable, config
file, built-in default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


CONFIG_ENV_VAR = "LOGSEQ_CLI_CONFIG"


class ConfigError(Exception):
    """Raised when a config file exists but cannot be used."""


def config_search_paths() -> list[Path]:
    """Where a config file is looked for, in order of precedence.

    ``LOGSEQ_CLI_CONFIG`` wins when set, so a test or a second graph can point
    at its own file without touching the user's.
    """
    explicit = os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        return [Path(explicit).expanduser()]

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return [
        base / "logseq-cli" / "config.toml",
        Path.home() / ".logseq-cli.toml",
    ]


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Read the config file, or return an empty dict when there is none.

    Not having a config file is normal and silent. A file that exists but is
    unreadable or malformed raises: the user meant to configure something, so
    failing quietly would hide their mistake.

    Three ways of not finding one, deliberately treated differently:
    an explicit ``path`` argument that does not exist raises, because the
    caller named that file; ``LOGSEQ_CLI_CONFIG`` pointing at a missing file
    warns and continues, because a stale variable should not stop commands
    that need no settings; searching the default locations and finding
    nothing is silent.
    """
    if path is not None:
        candidates = [Path(path).expanduser()]
        explicit = True
    else:
        candidates = config_search_paths()
        explicit = bool(os.environ.get(CONFIG_ENV_VAR))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        if tomllib is None:  # pragma: no cover - only on 3.10 without tomli
            raise ConfigError(
                f"Found {candidate}, but no TOML parser is available. "
                "Python 3.11+ has one built in; on 3.10 install 'tomli'."
            )
        try:
            with candidate.open("rb") as handle:
                data = tomllib.load(handle)
        except OSError as exc:
            raise ConfigError(f"Cannot read {candidate}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{candidate} is not valid TOML: {exc}") from exc
        if not isinstance(data, dict):  # pragma: no cover - TOML root is a table
            raise ConfigError(f"{candidate} must contain a TOML table")
        data["_path"] = str(candidate)
        return data

    if path is not None:
        # An explicit argument is a direct instruction: the caller named this
        # file, so not finding it is an error, not a fallback.
        raise ConfigError(f"No config file at {candidates[0]}")

    if explicit:
        # LOGSEQ_CLI_CONFIG points somewhere that does not exist: a deleted,
        # renamed or mistyped path. Warn, but carry on without a config — most
        # commands need none, and taking the whole CLI down over a stale
        # variable helps nobody. A command that does need a setting still
        # fails loudly through require(), naming the setting.
        click.echo(
            f"warning: {CONFIG_ENV_VAR} points at {candidates[0]}, "
            "which does not exist; continuing without a config file",
            err=True,
        )
    return {}


def get(config: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    """Read ``[section] key`` from a loaded config, or ``default``."""
    value = config.get(section)
    if isinstance(value, dict):
        return value.get(key, default)
    return default


def resolve_heading(config: dict[str, Any], heading: str | None) -> str | None:
    """Turn a ``--under-heading`` value into the heading to write under.

    A name defined in ``[journal.headings]`` resolves to its heading, so
    ``--under-heading tasks`` can stand for ``## Tasks``. Anything else is
    passed through untouched, which keeps a literal ``"## Tasks"`` working and
    means an unconfigured CLI behaves exactly as before.

    With no value, the default comes from ``LOGSEQ_JOURNAL_HEADING`` first (it
    predates this file and stays authoritative), then ``[journal]
    default_heading``. Without either, the caller inserts at top level.
    """
    if heading is None:
        from_env = os.environ.get("LOGSEQ_JOURNAL_HEADING")
        if from_env:
            return from_env
        return get(config, "journal", "default_heading")

    journal = config.get("journal")
    if isinstance(journal, dict):
        named = journal.get("headings")
        if isinstance(named, dict) and heading in named:
            return str(named[heading])
    return heading


def require(config: dict[str, Any], section: str, key: str, needed_for: str) -> Any:
    """Read a value that has no sensible default, or explain what is missing.

    Used for the handful of settings that describe the user's own graph. The
    message names the setting, the file it belongs in, and what asked for it,
    so the fix does not require reading the source.
    """
    value = get(config, section, key)
    if value not in (None, ""):
        return value

    where = config.get("_path") or config_search_paths()[0]
    raise ConfigError(
        f"{needed_for} needs '{key}' under [{section}] in your config.\n"
        f"  expected in: {where}\n"
        f"  see config.example.toml in the repository for the format.\n"
        f"  this setting describes your own graph, so there is no default."
    )
