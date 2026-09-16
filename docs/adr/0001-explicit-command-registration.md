# Commands are registered by an explicit import list

`logseq_cli/cli.py` imports each command module by name, and
`logseq_cli/commands/__init__.py` stays empty. Importing a command module
registers its commands as a side effect, because each one decorates against the
group in `logseq_cli/group.py`; `group.py` imports nothing from `commands/`, so
the dependency runs one way and there is no cycle.

## Considered Options

**Discovering modules with `pkgutil.iter_modules`.** A new command module would
register itself, and the import list would never need editing. Rejected: a
module that appears by directory scan has no place where its existence is
written down, and a typo in a filename then presents as a missing command
rather than as an import error. The registry test
(`tests/test_command_registry.py`) would report the symptom and not the cause.

**Keeping the group in `cli.py` and importing the command modules at the bottom
of the file.** This avoids editing the test suite, which patches the API client
by module path. Rejected: it introduces a circular import on purpose. A cycle
that has to be explained in a comment is worse than one mechanical edit across
the tests.

## Consequences

Adding a command module means adding one line to `cli.py`. Forgetting it means
the commands are absent, which is why the registry test asserts the full set of
command names rather than iterating whatever happens to be registered.
