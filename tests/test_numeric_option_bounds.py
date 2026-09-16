"""Every numeric option that takes a bound refuses a value below its minimum.

Four options validated their lower bound and four did not, and nothing recorded
why. The four that were safe were safe *because* someone had written a guard,
not because their code differed: removing the guard from `find-block` reproduces
the same defect the `get-backlinks` fix closed.

Only `get-backlinks` stated a false number, which is why it is the one that got
found. The other three answered a different question than the one asked without
saying so — the cutoff moved into the future, the oldest journal dropped out of
the sample, a suggestion disappeared — all with exit code 0.

The minimum differs per option and that is deliberate, not an oversight:

- ``0`` lifts the cap for ``get-backlinks --limit`` and ``get-todos
  --refs-limit``, so their boundary is ``< 0``.
- ``0`` is meaningless for ``find-block --limit`` and ``get-journal-range
  --tail/--limit`` — no caller wants zero matches — so theirs is ``< 1``.
- ``0`` asks for nothing at all from ``suggest-connections --max-suggestions``,
  and means "today only" for the two ``--days`` windows.

What they share is the floor: below zero no numeric option here has a meaning.
That is the part this file pins, for every such option the registry knows,
so a new one cannot quietly skip the convention.
"""
import json

import click
import pytest
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from tests.conftest import split_runner


# Thresholds, not caps: both are compared with >= / < against a count, where a
# negative value means "no threshold" and loses nothing. They are listed here
# rather than detected, because the distinction is a judgement about meaning
# and not a property of the code.
THRESHOLDS = {
    ("find-knowledge-gaps", "min_refs"),
    ("suggest-connections", "min_shared"),
}


def _numeric_options():
    """Every integer option in the registry, as (command, param) pairs."""
    for name, command in sorted(cli.commands.items()):
        for param in command.params:
            if isinstance(param, click.Option) and getattr(param.type, "name", "") == "integer":
                yield name, param


def _bounded_options():
    return [(n, p) for n, p in _numeric_options() if (n, p.name) not in THRESHOLDS]


# A command that will not run without them cannot be asked about its bounds:
# Click refuses the missing argument first, and the sweep would measure that
# refusal instead of the one under test.
REQUIRED_ARGS = {
    "find-block": ["--content", "x"],
    "get-backlinks": ["--name", "Alice"],
    "get-journal-range": ["--from", "2026-09-01", "--to", "2026-09-02"],
    "init": ["--dry-run"],
}


def _run(args):
    api = MagicMock()
    api.get_all_pages.return_value = []
    api.get_page_linked_references.return_value = []
    api.datascript_query.return_value = []
    api.get_page_blocks_tree.return_value = []
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args), api


class TestTheScanItself:
    """Guards the guard: a scan that finds nothing would pass every test below."""

    def test_the_registry_yields_the_known_numeric_options(self):
        found = {(n, p.name) for n, p in _numeric_options()}
        for expected in (("get-backlinks", "limit"), ("find-block", "limit"),
                         ("get-todos", "refs_limit"), ("analyze-graph", "days"),
                         ("init", "days"), ("suggest-connections", "max_suggestions"),
                         ("get-journal-range", "tail")):
            assert expected in found, (
                f"{expected} exists but the scan missed it — the detection is "
                "broken, not the command"
            )

    def test_enough_options_are_bounded_to_make_the_sweep_meaningful(self):
        assert len(_bounded_options()) >= 8, (
            f"only {len(_bounded_options())} bounded options found; the type "
            "detection probably stopped matching"
        )

    def test_every_named_threshold_still_exists(self):
        """The other direction: an exception for an option nobody has any more
        would silently excuse a future option that inherits the name."""
        known = {(n, p.name) for n, p in _numeric_options()}
        stale = THRESHOLDS - known
        assert not stale, f"exempted options that no longer exist: {sorted(stale)}"


class TestNegativeValuesAreRefused:
    @pytest.mark.parametrize("command,param",
                             [(n, p) for n, p in _bounded_options()],
                             ids=lambda v: v if isinstance(v, str) else v.name)
    def test_a_negative_value_is_rejected(self, command, param):
        flag = param.opts[0]
        result, _ = _run([command, flag, "-1"] + REQUIRED_ARGS.get(command, []))
        assert result.exit_code != 0, (
            f"{command} {flag} -1 was accepted; every bounded numeric option "
            "must refuse a value below zero"
        )

    @pytest.mark.parametrize("command,param",
                             [(n, p) for n, p in _bounded_options()],
                             ids=lambda v: v if isinstance(v, str) else v.name)
    def test_the_refusal_names_the_option_and_keeps_stdout_clean(self, command, param):
        flag = param.opts[0]
        result, _ = _run([command, flag, "-1"] + REQUIRED_ARGS.get(command, []))
        assert flag in result.stderr, (
            f"{command} {flag} -1 was refused without naming {flag}: "
            f"{result.stderr!r}"
        )
        assert result.stdout == "", (
            f"{command} {flag} -1 wrote to stdout, which is reserved for payload"
        )


class TestTheRefusalCostsNoReads:
    """Validation belongs before the first API call, not after it."""

    @pytest.mark.parametrize("command,param",
                             [(n, p) for n, p in _bounded_options()],
                             ids=lambda v: v if isinstance(v, str) else v.name)
    def test_nothing_is_fetched_before_the_value_is_refused(self, command, param):
        flag = param.opts[0]
        _, api = _run([command, flag, "-1"] + REQUIRED_ARGS.get(command, []))
        calls = [c for c in api.mock_calls if not c[0].startswith("_")]
        assert not calls, (
            f"{command} {flag} -1 hit the API before refusing: {calls[:3]}"
        )


class TestTheDocumentedMeaningOfZeroSurvives:
    """The guards must not have quietly turned 0 into an error."""

    @pytest.mark.parametrize("command,flag", [
        ("get-backlinks", "--limit"),
        ("get-todos", "--refs-limit"),
        ("suggest-connections", "--max-suggestions"),
        ("analyze-graph", "--days"),
        ("init", "--days"),
    ])
    def test_zero_is_accepted(self, command, flag):
        result, _ = _run([command, flag, "0"] + REQUIRED_ARGS.get(command, []))
        assert result.exit_code == 0, (
            f"{command} {flag} 0 was refused: {result.stderr!r}"
        )

    @pytest.mark.parametrize("command,flag", [
        ("find-block", "--limit"),
        ("get-journal-range", "--tail"),
    ])
    def test_zero_is_refused_where_it_has_no_meaning(self, command, flag):
        result, _ = _run([command, flag, "0"] + REQUIRED_ARGS.get(command, []))
        assert result.exit_code != 0, (
            f"{command} {flag} 0 was accepted, but zero matches is not an answer "
            "anyone asks for"
        )
