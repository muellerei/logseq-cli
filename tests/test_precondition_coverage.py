"""Every command that takes a precondition checks it before anything else.

A command that gains an ``--expect-*`` option and forgets to call the check
would accept the option and ignore it: the caller believes it is protected.
The commands are found by their options, so a new one cannot stay out. Each
needs a recipe below (a graph, and a call that would change it); the checks
are the same for all: a wrong precondition refuses without a write, also under
``--dry-run`` and where the call would change nothing.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

TASK = "00000000-0000-4000-8000-0000000000a1"
OTHER = "00000000-0000-4000-8000-0000000000a2"
# Per option, a value the recipe's block does not match (it is a TODO).
WRONG = {"--expect-hash": "0123456789ab", "--expect-marker": "DONE",
         "--expect-tree-hash": "0123456789ab"}

# command -> (pages, options naming a change, options naming no change or None, has --dry-run)
RECIPES = {
    "set-todo-status": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]},
        ["--id", TASK, "--status", "DONE"],
        ["--id", TASK, "--status", "TODO"],
        True),
    "update-block": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]},
        ["--id", TASK, "--content", "TODO ship the parser, fixed"],
        None,
        True),
    "set-block-property": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]},
        ["--id", TASK, "--key", "prio", "--value", "high"],
        None,
        True),
    "remove-property": (
        {"Probe Page": [{"content": "TODO ship the parser\nprio:: high", "marker": "TODO",
                         "uuid": TASK}]},
        ["--id", TASK, "--key", "prio"],
        None,
        True),
    "move-block": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK,
                         "children": [{"content": "a child"}]},
                        {"content": "the new parent", "uuid": OTHER}]},
        ["--id", TASK, "--under", OTHER],
        None,
        True),
    "copy-block": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK,
                         "children": [{"content": "a child"}]}],
         "Other Page": [{"content": "already here"}]},
        ["--id", TASK, "--to-page", "Other Page", "--remove"],
        None,
        True),
    # The precondition is for the block the upsert replaces, found by its heading.
    "add-journal-block": (
        {"2099-01-05, Monday": [{"content": "## Log", "children": [
            {"content": "### Carol\nTODO ship the parser", "marker": "TODO", "uuid": TASK}]}]},
        ["--date", "2099-01-05", "--under-heading", "## Log", "--upsert-heading", "### Carol",
         "--content", "### Carol\nTODO ship the parser, fixed"],
        None,
        True),
    # The alias is a second name for the same command and must carry the same gate.
    "remove-block": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]},
        ["--id", TASK],
        None,
        True),
    "delete-block": (
        {"Probe Page": [{"content": "TODO ship the parser", "marker": "TODO", "uuid": TASK}]},
        ["--id", TASK],
        None,
        True),
}


def _expecting_commands():
    """Command name -> its ``--expect-*`` options, from the registry."""
    found = {}
    for name, command in cli.commands.items():
        options = [opt for p in command.params for opt in p.opts if opt.startswith("--expect-")]
        if options:
            found[name] = options
    return found


# The commands that change a block they read, and so take a precondition.
# `delete-block` is a second name for `remove-block`, not a ninth command.
PRECONDITION_COMMANDS = {
    "set-todo-status", "update-block", "set-block-property", "remove-property",
    "move-block", "copy-block", "add-journal-block", "remove-block",
}


def test_exactly_these_commands_take_a_precondition():
    """A command that loses its option, or gains one unannounced, shows here."""
    names = set(_expecting_commands())
    assert names - {"delete-block"} == PRECONDITION_COMMANDS
    assert cli.commands["delete-block"] is cli.commands["remove-block"]


def test_every_command_with_a_precondition_has_a_recipe():
    assert set(_expecting_commands()) <= set(RECIPES), (
        "a command takes an --expect-* option and this test does not try it: add a recipe")


CASES = [(name, option, variant)
         for name, options in sorted(_expecting_commands().items()) if name in RECIPES
         for option in options
         for variant in ("write", "dry-run", "no-change")]


@pytest.mark.parametrize("name,option,variant", CASES,
                         ids=[f"{n}{o}-{v}" for n, o, v in CASES])
def test_a_wrong_precondition_refuses_without_a_write(monkeypatch, name, option, variant):
    pages, change, no_change, has_dry_run = RECIPES[name]
    if variant == "dry-run" and not has_dry_run:
        pytest.skip("no --dry-run")
    if variant == "no-change" and no_change is None:
        pytest.skip("no call that changes nothing")
    double = LogseqHttpDouble.installed(monkeypatch, pages)
    before = double.snapshot()
    args = {"write": change, "dry-run": [*change, "--dry-run"], "no-change": no_change}[variant]
    result = split_runner().invoke(cli, ["--token", "t", name, *args, option, WRONG[option], "--json"])
    assert result.exit_code != 0 and result.stdout == "", result.stdout
    assert json.loads(result.stderr)["reason"] == "precondition_failed"
    assert double.writes() == []
    assert double.snapshot() == before
