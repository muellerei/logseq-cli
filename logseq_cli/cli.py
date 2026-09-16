"""Console entry point. Importing this module registers every command.

Each command module decorates against the group in :mod:`logseq_cli.group`, so
a command exists only once its module has been imported. That is what the list
below does, and it is the reason a test takes the group from here rather than
from ``logseq_cli.group``: imported directly, the group holds whatever happens
to have been imported so far — the full registry when the whole suite runs, and
a partial one when a file runs alone.

The list is explicit rather than a directory scan. ``docs/adr/0001`` records
why; the short version is that a typo in a filename should be an import error,
not a command that silently does not exist.
"""
from logseq_cli.group import cli
from logseq_cli.commands import (  # noqa: F401  imported for registration
    analysis,
    blocks,
    edit,
    journal,
    meta,
    pages,
    properties,
    query,
    todos,
)


def main():
    cli()


if __name__ == "__main__":
    main()
