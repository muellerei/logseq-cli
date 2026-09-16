"""The click group: global options, and the one place the API client is built.

Every command module decorates against the `cli` group defined here, so this
module must not import from `commands/` — that is what keeps registration a
one-way edge and the imports free of cycles.

It is also the only module that names LogseqAPI, which is why the test suite
patches `logseq_cli.group.LogseqAPI`: mock.patch replaces a name in the
namespace it is given, and this is the namespace the constructor is read from.

resolve_version() stays at this level deliberately. It finds pyproject.toml
through `Path(__file__).resolve().parent.parent`, so one directory deeper it
would silently fall back to the installed metadata instead — and in an editable
install that agrees with pyproject until the next version bump, which is to say
the test would keep passing while the answer went stale.
"""
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path

import click

from logseq_cli.api import LogseqAPI, InvalidPortError


def resolve_version() -> str:
    """Single source of truth for the CLI version.

    Reads pyproject.toml when running from a source checkout (the authoritative
    value during development), else falls back to the installed package metadata.
    Avoids the stale hardcoded-version drift that previously made --version lie.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                # version = "0.5.0"
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    try:
        return _pkg_version("logseq-cli")
    except PackageNotFoundError:
        return "unknown"


@click.group()
@click.version_option(version=resolve_version(), prog_name="logseq-cli")
@click.option("--host", default=None, help="Logseq API host (default: 127.0.0.1)")
@click.option("--port", default=None, help="Logseq API port (default: 12315)")
@click.option("--token", default=None, help="Logseq API Bearer token")
@click.option("--no-cache", "no_cache", is_flag=True, help="Bypass the in-memory read cache for this invocation")
@click.pass_context
def cli(ctx, host, port, token, no_cache):
    """CLI for Logseq knowledge graph - pages, journals, blocks, search, and graph analysis."""
    ctx.ensure_object(dict)
    try:
        api = LogseqAPI(host=host, port=port, token=token)
    except InvalidPortError as e:
        # Raised before any request. A traceback here would be worse than the
        # unchecked value was: the group callback runs ahead of every command,
        # so this is the first thing a user sees, including under --json.
        raise click.ClickException(str(e)) from None
    if no_cache:
        api.cache_enabled = False
    ctx.obj["api"] = api
