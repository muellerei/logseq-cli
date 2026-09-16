"""The Registry holds every Command Name, and this file says which ones.

Spec 001 splits `cli.py` into nine command modules that `cli.py` imports by
name. A module nobody imports registers nothing, and the failure is silent:
the Registry is simply short a few Command Names and the CLI starts fine.
The same is true of a second Command Name left behind when its Command moves.

`test_counters_sum_to_the_number_of_commands` in
`tests/test_readme_documents_options.py` does go red on a missing module, but
it reports "counters sum to 37, registry has 29" — a number, from a test about
the README. This file names the Command Name that went missing, and it covers
`delete-block`, which that test excludes by design.

The set is literal on purpose. Deriving it from `cli.commands` would assert
that the Registry equals itself: a module that is never imported leaves no
trace to iterate over. A count would hold as long as nothing is added in the
same commit, and when it failed it would not say which name went.

The cost is a deliberate line here for every new Command Name, next to the
README row and the CHANGELOG entry that spec 007 already asks for.

One thing the registry assertion alone cannot do, measured rather than
assumed: run on its own it goes red when a module drops out of the import
list, but in the full suite it stays green for four of the nine. Other test
files import `logseq_cli.commands.blocks`, `.properties`, `.analysis` and
`.pages` directly to reach a helper, and importing a command module registers
its commands as a side effect. By the time this file runs, the registry has
been filled by somebody else. `test_every_command_module_is_imported_by_the_entry_point`
reads the import list itself, so it does not depend on what ran before it.
"""
import ast
import pathlib

import logseq_cli.cli
from logseq_cli.cli import cli

# 38 Command Names for 37 Commands: `delete-block` is a second name for
# `remove-block`, not a Command of its own.
EXPECTED = {
    "add-block-ref",
    "add-journal-block",
    "add-journal-content",
    "add-journal-entry",
    "add-note-content",
    "analyze-graph",
    "analyze-journal-patterns",
    "copy-block",
    "create-page",
    "delete-block",
    "delete-page",
    "doctor",
    "find-block",
    "find-knowledge-gaps",
    "get-all-pages",
    "get-backlinks",
    "get-block",
    "get-journal-range",
    "get-journal-summary",
    "get-page",
    "get-page-stats",
    "get-properties",
    "get-todos",
    "init",
    "insert-block",
    "move-block",
    "query-pages-by-property",
    "remove-block",
    "remove-property",
    "rename-page",
    "replace-text",
    "search-pages",
    "set-block-property",
    "set-property",
    "set-todo-status",
    "smart-query",
    "suggest-connections",
    "update-block",
}


def test_every_command_name_is_registered():
    assert set(cli.commands) == EXPECTED


def test_every_command_module_is_imported_by_the_entry_point():
    """The import list in cli.py names every module under commands/.

    Asserted against the source rather than against `sys.modules`: a module
    another test imported for a helper is loaded either way, so a registry
    that looks complete says nothing about the entry point.
    """
    entry = pathlib.Path(logseq_cli.cli.__file__)
    imported = {
        alias.name
        for node in ast.parse(entry.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ImportFrom) and node.module == "logseq_cli.commands"
        for alias in node.names
    }
    on_disk = {
        p.stem
        for p in (entry.parent / "commands").glob("*.py")
        if p.stem != "__init__"
    }
    assert imported == on_disk, (
        f"not imported by cli.py: {sorted(on_disk - imported)}; "
        f"imported but no such module: {sorted(imported - on_disk)}. "
        f"A command module that cli.py does not import registers nothing, and "
        f"the CLI starts without its commands."
    )
