"""The Example lines in each command's --help must be calls the command takes.

``set-block-property --help`` showed ``--key "id"``, a key Logseq reads as the
block's id: on re-read the value became the block's uuid (#51). The key rule
refused only ``custom-id`` then, so the example went through; it is refused
now. The example parsed, so a check of the parser alone would not have seen
it: the refusal is the command's own key rule. Both are checked here. Every example must parse, which catches an
option renamed or removed under it; and every key an example writes goes
through the rule the command applies, which catches #51.

Examples are shell lines, in the epilog and in the command's docstring, and a
call may sit inside one: ``U=$(logseq-cli ...)`` or ``VAR=1 logseq-cli ...``.
A ``$(...)`` substitution stands for one argument and a pipe ends the call, so
both are cut down to what the CLI would receive.
"""
import re
import shlex

import click
import pytest

from logseq_cli.cli import cli
from logseq_cli.helpers import normalize_property_key

# Commands whose --key is a key they write. remove-property takes any key, so
# that one stored before the rule existed can still be removed, and
# query-pages-by-property reads.
KEY_WRITERS = {"set-property", "set-block-property"}


# A call at the start of a line, after "$(" or after an environment assignment.
_CALL_RE = re.compile(r'^(?:[A-Z_]+=\S*\s+|\w+=\$\()?(logseq-cli .*)$')


def _examples():
    for name, command in sorted(cli.commands.items()):
        lines = ((command.help or "") + "\n" + (command.epilog or "")).splitlines()
        i = 0
        while i < len(lines):
            match = _CALL_RE.match(lines[i].strip())
            if match:
                line = match.group(1)
                while line.endswith("\\") and i + 1 < len(lines):
                    i += 1
                    line = line[:-1] + " " + lines[i].strip()
                if "$(" in lines[i].strip()[:match.start(1)] and line.endswith(")"):
                    line = line[:-1]
                yield name, line
            i += 1


def _argv(example):
    code = example.split(" | ")[0].split("  #")[0]
    code = re.sub(r"\$\([^)]*\)", "X", code)
    argv = shlex.split(code)[1:]
    if argv[:1] == ["--token"]:
        argv = argv[2:]
    return argv


EXAMPLES = list(_examples())


def test_the_scan_finds_the_examples():
    """Guards the guard: a scan that finds nothing passes every check below."""
    assert len(EXAMPLES) > 50
    assert any(name == "set-block-property" for name, _ in EXAMPLES)


@pytest.mark.parametrize("name, example", EXAMPLES, ids=[e for _, e in EXAMPLES])
def test_every_example_parses(name, example):
    argv = _argv(example)
    command = cli.commands[argv[0]]
    try:
        command.make_context(argv[0], argv[1:])
    except click.exceptions.Exit:
        pass


def _writes_properties(name):
    """--property KEY=VALUE, repeatable, on a command that writes. get-properties
    has a --property too, a single key it reads."""
    return any(p.name == "properties" and p.multiple for p in cli.commands[name].params)


KEY_EXAMPLES = [(n, e) for n, e in EXAMPLES
                if n in KEY_WRITERS or (_writes_properties(n) and "--property" in e)]


def test_the_key_scan_finds_the_set_block_property_example():
    assert any(n == "set-block-property" for n, _ in KEY_EXAMPLES)


@pytest.mark.parametrize("name, example", KEY_EXAMPLES, ids=[e for _, e in KEY_EXAMPLES])
def test_every_key_an_example_writes_is_one_the_command_takes(name, example):
    argv = _argv(example)
    ctx = cli.commands[argv[0]].make_context(argv[0], argv[1:])
    keys = []
    if argv[0] in KEY_WRITERS:
        keys.append(ctx.params["key"])
    keys += [pair.split("=", 1)[0] for pair in ctx.params.get("properties") or ()]
    assert keys, "the example writes no key; the filter above is wrong"
    for key in keys:
        normalize_property_key(key)
