"""What `handle_connection_error` has to carry across, and why it is asserted here.

`tests/test_dry_run_coverage.py` holds the `--dry-run` guarantee across every
writing command. It finds those commands by unwrapping each callback and parsing
the module `__module__` names. That only works while the wrapper carries the
wrapped function's metadata rather than its own.

A wrapper built by hand does not. Before this was fixed, the decorator copied
`__name__` and `__doc__` and nothing else, so every callback reported the module
that defines the decorator. While decorator and commands shared one file the two
were indistinguishable; once the commands moved to `logseq_cli/commands/`, the
scan would have looked in the wrong file and found no writing command at all —
and said so by passing.

So the dry-run test does notice. What it cannot do is say why: it reports that
`create-page` and sixteen others write without offering `--dry-run`, which reads
as a defect in the commands rather than in the decorator. This file asserts the
property directly, so the failure names the cause.

The commands are decorated at import time and cannot be undecorated, so the
assertions run against a function defined here: the decorator is applied to a
local function, and what comes back has to point at this module.
"""
import functools

from logseq_cli.cli import cli
from logseq_cli.output import handle_connection_error


def _probe(ctx=None, as_json=False):
    """A stand-in command. Only its metadata matters."""
    return "probe"


def test_the_wrapper_keeps_the_module_of_the_function_it_wraps():
    wrapped = handle_connection_error(_probe)
    assert wrapped.__module__ == __name__, (
        "the wrapper reports the decorator's module, not the wrapped "
        "function's — tests/test_dry_run_coverage.py parses the file "
        "__module__ names, so it would scan logseq_cli/output.py and find "
        "no command at all"
    )


def test_the_wrapper_exposes_the_original_function():
    wrapped = handle_connection_error(_probe)
    assert getattr(wrapped, "__wrapped__", None) is _probe, (
        "__wrapped__ is missing, so unwrapping a command callback stops at "
        "the wrapper and its source is the decorator's, not the command's"
    )


def test_the_wrapper_still_carries_name_and_docstring():
    wrapped = handle_connection_error(_probe)
    assert wrapped.__name__ == "_probe"
    assert wrapped.__doc__ == _probe.__doc__


def test_every_command_callback_unwraps_to_its_own_module():
    """The property the dry-run scan actually relies on, across the registry."""
    wrong = {}
    for name, command in cli.commands.items():
        func = command.callback
        while hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        if func.__module__ == "logseq_cli.output":
            wrong[name] = func.__module__
    assert not wrong, (
        f"{sorted(wrong)} report logseq_cli.output as their module, which is "
        f"where the decorator lives, not where the command is defined"
    )
