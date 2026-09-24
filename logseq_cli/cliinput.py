"""A write's text from the command line: --content, --content-file, --tree.

The commands that take a block's text share these, so that wherever they are
used an empty ``--content``, an unreadable file or malformed tree JSON is
refused the same way, with ``click.BadParameter`` and before any write. Nothing here talks to
Logseq, so all of it is tested without a mock; the layering tests keep it that
way (ADR 0003).
"""

import sys
import json
from pathlib import Path

import click

from logseq_cli.outlinetext import parse_hierarchical_content


def _normalize_json_tree(nodes) -> list:
    """Coerce a JSON-loaded tree into the shape ``parse_hierarchical_content`` produces.

    Each node must have a ``content`` string; ``children`` defaults to an empty list.
    """
    if not isinstance(nodes, list):
        raise click.BadParameter("Tree JSON must be an array of node objects.")
    out = []
    for node in nodes:
        if not isinstance(node, dict) or "content" not in node:
            raise click.BadParameter(
                "Tree JSON node must be an object with a 'content' field."
            )
        children = node.get("children") or []
        out.append({
            "content": str(node["content"]),
            "children": _normalize_json_tree(children),
        })
    return out


def parse_tree_input(raw: str) -> list:
    """Parse tree input from either JSON or tab-indented text.

    Auto-detection: first non-whitespace character ``[`` or ``{`` is treated as
    JSON, otherwise the input is fed through :func:`parse_hierarchical_content`.
    Returns a list of ``{"content": str, "children": [...]}`` nodes.
    """
    if raw is None:
        return []
    stripped = raw.lstrip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON tree: {e}")
        if isinstance(data, dict):
            data = [data]
        return _normalize_json_tree(data)
    return parse_hierarchical_content(raw)


def require_content(content: str, option: str = "--content") -> str:
    """Reject content that is empty or only whitespace, before any write.

    The mirror of the guard in :func:`read_content_file`, for text arriving on
    the command line. ``--content "$(cat missing.md)"`` collapses to an empty
    string when the substitution fails, and the shell reports that on stderr
    while still exiting 0 -- so without this check the CLI writes an empty
    block and reports success. A block with no content is never the intent,
    which is why this is an error rather than a warning.

    Returns the content unchanged, so callers can wrap the value in place.
    """
    if not content.strip():
        raise click.BadParameter(f"{option} is empty")
    return content


def read_content_file(path: str, option: str = "--content-file") -> str:
    """Read block content from a file, for ``--content-file``.

    The file is read as UTF-8 and returned as written (minus a trailing
    newline), so tab-indented hierarchies and flush top-level bullets survive
    unchanged. Unlike ``--content``, no shell quoting sits between the text and
    the CLI, which is why this is the safe path for content with apostrophes,
    quotes or umlauts. Two things are not text and go: a BOM, and the ``\\r``
    of a CRLF or CR line ending.

    ``-`` reads stdin instead, the convention every Unix tool shares: content
    that is already in a pipe would otherwise need a temporary file, which is
    the one detour this option exists to remove. A file literally named ``-``
    is then unreachable — the convention wins, and ``./-`` still names the file.
    stdin is read as bytes and decoded here, like the file: ``sys.stdin``
    decodes by the locale and keeps ``\\r``, so under ``LC_ALL=C`` invalid
    bytes went on as lone surrogates, and a pipe wrote what the same file
    did not.

    Raises :class:`click.BadParameter` for a missing, unreadable, non-UTF-8 or
    effectively empty file, so the caller fails before any write. ``option``
    is the flag the message names: ``insert-block --tree-file`` reads through
    here too.
    """
    if path == "-":
        where = "stdin"
        data = sys.stdin.buffer.read()
    else:
        where = path
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            raise click.BadParameter(f"{option} not found: {path}")
        except IsADirectoryError:
            raise click.BadParameter(f"{option} is a directory: {path}")
        except OSError as e:
            raise click.BadParameter(f"{option} cannot be read: {path} ({e})")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise click.BadParameter(f"{option} is not valid UTF-8: {where} ({e})")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        raise click.BadParameter(f"{option} is empty: {where}")
    return raw.rstrip("\n")


def content_or_file(content, content_file, *, required=True):
    """The text of ``--content`` or of ``--content-file``, whichever was given.

    One place for the either/or, so the commands that use it answer both,
    neither and a bad file the same way. ``insert-block`` passes
    ``required=False``: ``--tree`` is its other source, and it words "neither"
    itself. The file's text is
    returned as if it had been passed as ``--content``: what the command does
    with it is unchanged. ``add-journal-block`` does not use this, since its
    ``--content-file`` means a tree, not ``--content``.
    """
    if content_file is None:
        if content is None and required:
            raise click.UsageError("Missing option '--content' (or '--content-file').")
        return content
    if content is not None:
        raise click.UsageError("Specify either --content or --content-file, not both.")
    return read_content_file(content_file)
